"""Where the console's chatter goes: the sound card, or the board speaker.

Two very different devices, each behind the smallest interface that fits it.

**The sound card** plays 16-bit mono PCM, and is reached through whatever
this machine has, best first:

* ``sounddevice`` (the ``voice`` extra) — a callback stream with the lowest
  latency, everywhere but macOS, where opening an output stream through
  PortAudio raises a media-library permission prompt (the same rule
  :mod:`tools.voice_mode` keeps);
* a **player reading raw PCM on stdin**, started once and kept open while
  the console talks: ``paplay`` (PulseAudio, and PipeWire through its pulse
  server), ``aplay`` (ALSA), or SoX's ``play`` (anywhere SoX is installed);
* on Windows, ``winsound`` playing short WAV chunks from memory;
* on macOS, ``afplay`` playing short WAV chunks from temporary files.

**The board speaker** — the PC speaker on the motherboard, the thing a BIOS
beeps through — is a square wave at one frequency at a time, on or off. It
is reached through the Linux PC-speaker input device (``EV_SND``/
``SND_TONE``, what the ``beep`` utility writes), or the console's tone ioctl
(``KIOCSOUND``) when the console is running on a Linux virtual terminal, or
``winsound.Beep`` on Windows. A Mac has no board speaker.

Nothing here raises to its caller. Every opener returns the device or a
sentence saying why there is none, because the console has one place to put
a failure — the notice line — and a chatter that cannot sound must cost the
reader a switch that says so, never a console that will not start.
"""

from __future__ import annotations

import glob
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Callable, List, Optional, Sequence, Tuple

#: The rate everything is synthesised at. Low enough that pure-Python
#: synthesis is cheap, high enough for a voice that is all upper partials.
SAMPLE_RATE = 22050

#: Raw-PCM players, in the order they are tried.
PLAYERS = ("paplay", "aplay", "play")


# ── The sound card ───────────────────────────────────────────────────────


def player_command(player: str, rate: int = SAMPLE_RATE) -> Optional[List[str]]:
    """The argv that makes ``player`` play signed 16-bit mono PCM from stdin.

    Each asks for a short device buffer: the chatter is paced to the text,
    and a player that buffered half a second would have it trailing the
    words it is meant to be saying.
    """
    if player == "paplay":
        return [
            "paplay", "--raw", "--format=s16le", f"--rate={rate}",
            "--channels=1", "--latency-msec=60",
            "--client-name=curie-chatter", "--stream-name=chatter",
        ]
    if player == "aplay":
        return [
            "aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate),
            "-c", "1", "--buffer-time=80000", "-",
        ]
    if player == "play":
        # SoX's player: format options describe the input, and ``play``
        # sends it to the default device on its own.
        return [
            "play", "-q", "-t", "raw", "-r", str(rate), "-e", "signed",
            "-b", "16", "-c", "1", "-",
        ]
    return None


def wav_bytes(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """``pcm`` wrapped in a WAV header, for the players that want a file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


class Sink:
    """Somewhere PCM can be written.

    ``streaming`` sinks take a write and play it in the background, so the
    caller paces itself to the clock; chunked ones block for the length of
    what they were handed, so the caller hands them a little at a time.
    """

    name = ""
    streaming = True

    def write(self, pcm: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def drop(self) -> None:
        """Forget anything written and not yet heard, where that is possible."""

    def close(self) -> None:
        """Release the device."""


class SoundDeviceSink(Sink):
    """A PortAudio output stream fed from a buffer the callback drains."""

    streaming = True

    def __init__(self, sd, rate: int = SAMPLE_RATE) -> None:
        self.name = "the sound card (sounddevice)"
        self._buffer = bytearray()
        self._lock = threading.Lock()

        def callback(outdata, frames, _time, _status) -> None:
            wanted = frames * 2
            with self._lock:
                chunk = bytes(self._buffer[:wanted])
                del self._buffer[:wanted]
            if len(chunk) < wanted:
                chunk += bytes(wanted - len(chunk))
            outdata[:] = chunk

        self._stream = sd.RawOutputStream(
            samplerate=rate, channels=1, dtype="int16", blocksize=512,
            callback=callback,
        )
        self._stream.start()

    def write(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer.extend(pcm)

    def drop(self) -> None:
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class PipeSink(Sink):
    """A player process reading raw PCM on its stdin, kept open between words."""

    streaming = True

    def __init__(self, argv: Sequence[str]) -> None:
        self.name = f"the sound card ({argv[0]})"
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def write(self, pcm: bytes) -> None:
        stdin = self._proc.stdin
        if stdin is None or not self.alive:
            raise OSError(f"{self.name} stopped")
        stdin.write(pcm)
        stdin.flush()

    def close(self) -> None:
        proc = self._proc
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class WinsoundSink(Sink):
    """Windows: each chunk played synchronously from memory."""

    streaming = False

    def __init__(self, winsound_module, rate: int = SAMPLE_RATE) -> None:
        self.name = "the sound card (winsound)"
        self._winsound = winsound_module
        self._rate = rate

    def write(self, pcm: bytes) -> None:
        self._winsound.PlaySound(
            wav_bytes(pcm, self._rate), self._winsound.SND_MEMORY
        )


class AfplaySink(Sink):
    """macOS: each chunk played from a temporary WAV by ``afplay``."""

    streaming = False

    def __init__(self, rate: int = SAMPLE_RATE) -> None:
        self.name = "the sound card (afplay)"
        self._rate = rate

    def write(self, pcm: bytes) -> None:
        handle, path = tempfile.mkstemp(suffix=".wav", prefix="curie-chatter-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(wav_bytes(pcm, self._rate))
            subprocess.run(
                ["afplay", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def _sounddevice():
    """``sounddevice``, when it is installed and output through it is allowed."""
    if sys.platform == "darwin":
        return None
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception:
        return None
    return sd


def _has_module(name: str) -> bool:
    """Whether a module is installed, without importing it.

    Importing ``sounddevice`` starts PortAudio, which on Linux can print ALSA
    warnings straight onto file descriptor 2 — over the console's screen. A
    question about what *would* be used must not do that.
    """
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def soundcard_routes(platform: str = sys.platform) -> Tuple[str, ...]:
    """The ways to the sound card on ``platform``, best first.

    On Linux the pipe players lead: they run in their own process with their
    own stderr, where PortAudio's start-up chatter cannot reach the screen.
    """
    if platform == "win32":
        return ("sounddevice", "play", "winsound")
    if platform == "darwin":
        return ("play", "afplay")
    return ("paplay", "aplay", "play", "sounddevice")


_NO_SOUNDCARD = (
    "no way to reach the sound card — install a player (paplay, aplay or "
    "SoX's play) or the voice extra (`curie update --ensure voice`)"
)


def open_soundcard(
    rate: int = SAMPLE_RATE,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    platform: str = sys.platform,
) -> Tuple[Optional[Sink], str]:
    """The best sound-card output this machine has, or None and why not.

    A player that starts and dies at once — ``paplay`` with no sound server
    to talk to — is passed over for the next one rather than reported as the
    output, since the next one may well work.
    """
    failure = ""
    for route in soundcard_routes(platform):
        try:
            if route == "sounddevice":
                sd = _sounddevice()
                if sd is not None:
                    return SoundDeviceSink(sd, rate), ""
            elif route == "winsound":
                import winsound  # type: ignore[import-not-found]

                return WinsoundSink(winsound, rate), ""
            elif route == "afplay":
                if which("afplay") is not None:
                    return AfplaySink(rate), ""
            elif which(route) is not None:
                argv = player_command(route, rate)
                if argv is not None:
                    sink = PipeSink(argv)
                    time.sleep(0.05)
                    if sink.alive:
                        return sink, ""
                    sink.close()
                    failure = failure or f"{route} started and stopped at once"
        except Exception as exc:  # noqa: BLE001 - reported, then the next route
            failure = failure or f"{route} would not start ({exc})"
    return None, failure or _NO_SOUNDCARD


# ── The board speaker ────────────────────────────────────────────────────

#: Linux input-event constants for the PC speaker (``linux/input-event-codes.h``).
EV_SND = 0x12
SND_TONE = 0x02
#: The console tone ioctl and the PIT clock it divides (``linux/kd.h``).
KIOCSOUND = 0x4B2F
PIT_HZ = 1193180

#: The range a board speaker is worth driving over: below it is a rattle,
#: above it is a whistle a small cone barely reproduces.
BOARD_MIN_HZ = 110
BOARD_MAX_HZ = 3200


class Beeper:
    """A device that plays one square-wave tone at a time."""

    name = ""

    def tone(self, hz: float) -> None:  # pragma: no cover - interface
        """Start sounding ``hz`` (0 is silence) and return at once."""
        raise NotImplementedError

    def play(self, hz: float, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
        """Sound ``hz`` for ``seconds``, then stop."""
        self.tone(hz)
        try:
            sleep(max(0.0, seconds))
        finally:
            self.tone(0)

    def close(self) -> None:
        """Silence the speaker and let it go."""


def clamp_board_hz(hz: float) -> int:
    return int(max(BOARD_MIN_HZ, min(BOARD_MAX_HZ, hz))) if hz > 0 else 0


class EvdevBeeper(Beeper):
    """The Linux PC-speaker input device (``platform-pcspkr``)."""

    _EVENT = "llHHi"

    def __init__(self, path: str) -> None:
        self.name = f"the board speaker ({path})"
        self._fd = os.open(path, os.O_WRONLY)

    def tone(self, hz: float) -> None:
        os.write(self._fd, struct.pack(self._EVENT, 0, 0, EV_SND, SND_TONE, clamp_board_hz(hz)))

    def close(self) -> None:
        try:
            self.tone(0)
        except Exception:
            pass
        try:
            os.close(self._fd)
        except Exception:
            pass


class ConsoleBeeper(Beeper):
    """The Linux console's tone ioctl, on the virtual terminal we run on."""

    def __init__(self, path: str) -> None:
        import fcntl  # noqa: F401 - availability check, POSIX only

        self.name = f"the board speaker ({path})"
        self._fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOCTTY", 0))

    def tone(self, hz: float) -> None:
        import fcntl

        hz = clamp_board_hz(hz)
        fcntl.ioctl(self._fd, KIOCSOUND, int(PIT_HZ / hz) if hz else 0)

    def close(self) -> None:
        try:
            self.tone(0)
        except Exception:
            pass
        try:
            os.close(self._fd)
        except Exception:
            pass


class WindowsBeeper(Beeper):
    """``winsound.Beep`` — blocking, so it plays rather than toggles."""

    name = "the board speaker (Windows Beep)"

    def __init__(self, winsound_module) -> None:
        self._winsound = winsound_module

    def tone(self, hz: float) -> None:  # pragma: no cover - not used
        pass

    def play(self, hz: float, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
        hz = clamp_board_hz(hz)
        ms = int(seconds * 1000)
        if hz and ms > 0:
            self._winsound.Beep(max(37, hz), ms)
        elif ms > 0:
            sleep(seconds)


def find_pcspkr(
    by_path: str = "/dev/input/by-path",
    sysfs: str = "/sys/class/input",
    devices: str = "/dev/input",
) -> Optional[str]:
    """The PC speaker's event device, if the kernel has one."""
    for path in sorted(glob.glob(os.path.join(by_path, "*pcspkr*event*"))):
        return path
    for name_file in sorted(glob.glob(os.path.join(sysfs, "event*", "device", "name"))):
        try:
            with open(name_file, encoding="utf-8", errors="replace") as handle:
                name = handle.read().strip()
        except OSError:
            continue
        if name.lower() in {"pc speaker", "pcspkr"}:
            event = os.path.basename(os.path.dirname(os.path.dirname(name_file)))
            return os.path.join(devices, event)
    return None


def console_vt(stream_fds: Sequence[int] = (0, 1, 2)) -> Optional[str]:
    """The Linux virtual terminal this console is running on, if it is one.

    ``/dev/tty3`` yes; ``/dev/pts/4`` — a terminal emulator — no: only a
    real console can ask the kernel for a tone.
    """
    for fd in stream_fds:
        try:
            name = os.ttyname(fd)
        except Exception:
            continue
        if re.fullmatch(r"/dev/tty\d+", name):
            return name
    return None


def open_board_speaker(
    *,
    platform: str = sys.platform,
    pcspkr: Callable[[], Optional[str]] = find_pcspkr,
    vt: Callable[[], Optional[str]] = console_vt,
) -> Tuple[Optional[Beeper], str]:
    """The board speaker, or None and why there is none."""
    if platform == "win32":
        try:
            import winsound  # type: ignore[import-not-found]

            return WindowsBeeper(winsound), ""
        except Exception as exc:  # noqa: BLE001
            return None, f"winsound is unavailable ({exc})"
    if platform == "darwin":
        return None, "a Mac has no board speaker — turn BOARD SPEAKER off to use the sound card"
    if not platform.startswith("linux"):
        return None, f"the board speaker is not reachable on {platform}"
    problems: List[str] = []
    device = pcspkr()
    if device:
        try:
            return EvdevBeeper(device), ""
        except PermissionError:
            problems.append(
                f"the PC speaker is {device}, but this user cannot write to "
                "it — add yourself to the group that owns it (often `input` "
                "or `beep`), or install the `beep` package's udev rule"
            )
        except OSError as exc:
            problems.append(f"the PC speaker at {device} would not open ({exc})")
    terminal = vt()
    if terminal:
        try:
            return ConsoleBeeper(terminal), ""
        except Exception as exc:  # noqa: BLE001
            problems.append(f"the console tone on {terminal} is unavailable ({exc})")
    if problems:
        return None, problems[0]
    return None, (
        "no PC speaker device — load the driver (`sudo modprobe pcspkr`) if "
        "the machine has one; a terminal emulator cannot reach the console's "
        "own tone, a text console can"
    )


def describe_output(
    board_speaker: bool,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    platform: str = sys.platform,
    pcspkr: Callable[[], Optional[str]] = find_pcspkr,
    vt: Callable[[], Optional[str]] = console_vt,
) -> Tuple[bool, str]:
    """What the chatter would sound through, without opening anything.

    For the settings pane: it is asked on every redraw, so it must not start
    a player, open a device or import an audio library. ``(True, name)``
    when there is a way, ``(False, why)`` when there is not.
    """
    if board_speaker:
        if platform == "win32":
            return True, WindowsBeeper.name
        if platform == "darwin":
            return False, "a Mac has no board speaker — turn BOARD SPEAKER off to use the sound card"
        if not platform.startswith("linux"):
            return False, f"the board speaker is not reachable on {platform}"
        device = pcspkr()
        if device and os.access(device, os.W_OK):
            return True, f"the board speaker ({device})"
        terminal = vt()
        if terminal:
            return True, f"the board speaker (console tone on {terminal})"
        if device:
            return False, (
                f"the PC speaker is {device}, but this user cannot write to "
                "it — add yourself to the group that owns it (often `input` "
                "or `beep`), or install the `beep` package's udev rule"
            )
        return False, (
            "no PC speaker device — load the driver (`sudo modprobe pcspkr`) "
            "if the machine has one"
        )
    for route in soundcard_routes(platform):
        if route == "sounddevice":
            if platform != "darwin" and _has_module("sounddevice"):
                return True, "the sound card (sounddevice)"
        elif route == "winsound":
            return True, "the sound card (winsound)"
        elif which(route) is not None:
            return True, f"the sound card ({route})"
    return False, _NO_SOUNDCARD


__all__ = [
    "BOARD_MAX_HZ",
    "BOARD_MIN_HZ",
    "Beeper",
    "PLAYERS",
    "SAMPLE_RATE",
    "Sink",
    "clamp_board_hz",
    "console_vt",
    "describe_output",
    "find_pcspkr",
    "open_board_speaker",
    "open_soundcard",
    "player_command",
    "soundcard_routes",
    "wav_bytes",
]
