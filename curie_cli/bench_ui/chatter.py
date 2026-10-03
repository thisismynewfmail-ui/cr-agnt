"""Chatter: an Animal Crossing–style voice for the text as it streams.

The villagers in Animal Crossing do not speak, they *babble*: every letter of
a line becomes a tiny pitched syllable, run together fast enough to sound like
talk in a language nobody knows. Chatter does the same to the console's output
while it streams. Each letter is a short voiced blip — shaped like the vowel
the letter is said on, started like its consonant, pitched by the letter and
the voice, wobbling a little at random — with spaces as breaths and
punctuation as pauses. A ``?`` lifts at the end, a ``!`` jumps.

**It follows the stream, not the finished text.** Syllables are spoken at the
voice's own pace while text keeps arriving, so the voice speeds up, slows and
falls quiet with the model. When the model writes faster than any voice could
read, it hurries, and if it still falls more than a second behind
(:data:`MAX_LAG`) it skips ahead to the newest words — so what it is saying is
always what is appearing on the screen, not a backlog of what already did.

**Different work, a different voice.** What the agent is doing changes how it
sounds, and always downwards — the answer is the voice itself; everything
around the answer is lower and a little altered (see :data:`VARIANTS` and
:data:`MOTIFS`):

* *thinking*, while its drawer is open — lower, softer, breathier, slower;
* *a tool being called* — lower and clipped, reading out the tool's name;
* *a tool finishing* — two quick falling blips;
* *code* in the answer — a quiet typewriter tick;
* *an error* — a falling "uh-oh";
* *a slow provider* — a low "hm?";
* *a compaction* — a long low hum.

**Two outputs.** The sound card gets synthesised PCM — pure Python, no audio
library needed to make it (see :mod:`curie_cli.bench_ui.speakers` for how it
is played). The board speaker gets the same syllables as square-wave beeps:
one tone at a time, at one volume, which is all a PC speaker can do.

Everything here is total. A device that will not open turns the chatter off
for the rest of the session with a sentence saying why, and nothing the
console does waits on a sound: :class:`Chatterbox` takes text and returns at
once, and speaks on a thread of its own.
"""

from __future__ import annotations

import math
import random
import sys
import threading
import time
import unicodedata
from array import array
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple, Union

from curie_cli.bench_ui import speakers
from curie_cli.bench_ui.speakers import SAMPLE_RATE

# ── Settings ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChatterSettings:
    """``ui.chatter`` in ``config.yaml``. Parsed (and clamped) by
    :func:`curie_cli.bench_ui.settings.read_settings`."""

    #: Whether the console chatters at all.
    enabled: bool = False
    #: Beep through the PC speaker instead of the sound card.
    board_speaker: bool = False
    #: Which voice — a name in :data:`VOICES`.
    voice: str = "sweet"
    #: TONE: semitones up or down from the voice's own pitch.
    pitch: int = 0
    #: Percent. The board speaker has one volume and ignores it.
    volume: int = 60
    #: Percent of the voice's own pace.
    speed: int = 100
    #: MATCH STREAM: pace the voice to the stream itself — a syllable as
    #: often as letters arrive — instead of to ``speed``.
    match_stream: bool = False
    #: Percent: how far the pitch wanders from syllable to syllable.
    wobble: int = 50
    #: Voice the thinking while its drawer is open.
    thinking: bool = True
    #: Chirp as tools start and finish.
    tools: bool = True


#: Each numeric setting's range and the step its ◄ ► buttons move it by.
RANGES: Dict[str, Tuple[int, int, int]] = {
    "pitch": (-12, 12, 1),
    "volume": (0, 100, 10),
    "speed": (50, 200, 10),
    "wobble": (0, 100, 10),
}


def clamp_setting(name: str, value) -> int:
    """A numeric chatter setting, forced into its range (default if unusable)."""
    low, high, _step = RANGES[name]
    default = getattr(ChatterSettings(), name)
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# ── Voices ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Voice:
    name: str
    title: str
    blurb: str
    #: Resting pitch, Hz.
    hz: float
    #: Vocal-tract scale: above 1 is a smaller, brighter throat.
    formant: float
    #: ``round`` (soft), ``reed`` (buzzy) or ``chip`` (an 8-bit square).
    timbre: str
    #: Pace, relative to the base syllable rate.
    speed: float
    #: How far this voice's pitch wanders, 0…1, before the WOBBLE setting.
    wobble: float


VOICES: Dict[str, Voice] = {
    voice.name: voice
    for voice in (
        Voice("sweet", "Sweet", "the classic villager — bright, round and quick",
              420.0, 1.18, "round", 1.00, 0.55),
        Voice("peppy", "Peppy", "high and fast, with a lot of bounce",
              540.0, 1.28, "round", 1.25, 0.85),
        Voice("sleepy", "Sleepy", "low and slow, soft round the edges",
              250.0, 0.95, "round", 0.75, 0.35),
        Voice("gruff", "Gruff", "a low growl with grit in it",
              165.0, 0.82, "reed", 0.90, 0.45),
        Voice("snooty", "Snooty", "measured and nasal, and barely moves",
              330.0, 1.05, "reed", 0.85, 0.20),
        Voice("chip", "Chip", "an 8-bit square wave, the nearest thing to a board speaker",
              440.0, 1.00, "chip", 1.10, 0.60),
    )
}

DEFAULT_VOICE = "sweet"


def get_voice(name: Optional[str]) -> Voice:
    return VOICES.get(str(name or "").strip().lower(), VOICES[DEFAULT_VOICE])


# ── What the agent is doing ──────────────────────────────────────────────


@dataclass(frozen=True)
class Variant:
    """How one kind of output bends the voice."""

    name: str
    #: Semitones from the voice. Everything but the answer is lower.
    semitones: float
    gain: float
    #: Pace relative to the voice.
    speed: float
    #: A timbre of its own, or "" for the voice's.
    timbre: str = ""
    #: White noise mixed in, 0…1 — a whisper at the top.
    breath: float = 0.0
    #: How much of the voice's wobble survives.
    wobble: float = 1.0
    blurb: str = ""


VARIANTS: Dict[str, Variant] = {
    variant.name: variant
    for variant in (
        Variant("answer", 0.0, 1.00, 1.00, blurb="the answer, in the voice itself"),
        Variant("thinking", -5.0, 0.55, 0.80, "round", 0.30, 1.30,
                "lower, softer and breathier — murmuring to itself"),
        Variant("tool", -3.0, 0.70, 1.25, "chip", 0.0, 0.25,
                "lower and clipped — reading out the tool it reaches for"),
        Variant("code", -2.0, 0.45, 1.60, "tick", 0.20, 0.50,
                "a quiet typewriter under code"),
    )
}

#: Event sounds: (semitones from the voice, seconds, gain, glide, timbre).
#: ``glide`` is the pitch change across the sound — a fall is negative.
MOTIFS: Dict[str, Tuple[Tuple[float, float, float, float, str], ...]] = {
    "tool_done": ((-4.0, 0.045, 0.55, 0.0, "chip"), (-8.0, 0.065, 0.45, -0.10, "chip")),
    "error": ((-6.0, 0.12, 0.85, -0.05, ""), (-11.0, 0.24, 0.80, -0.18, "")),
    "wait": ((-7.0, 0.15, 0.45, 0.14, "round"),),
    "compaction": ((-10.0, 0.60, 0.50, -0.28, "round"),),
    "question": ((1.0, 0.075, 0.80, 0.40, ""),),
    "exclaim": ((3.0, 0.060, 1.00, 0.06, ""),),
}

#: How often each event may sound, seconds — a stream of identical events
#: is one sound, not a drum roll.
MOTIF_SPACING = {"wait": 8.0, "compaction": 10.0, "error": 1.5}

# ── Letters ──────────────────────────────────────────────────────────────

#: Vowel formants, Hz (F1, F2): what makes an "ah" an "ah".
VOWEL_FORMANTS: Dict[str, Tuple[float, float]] = {
    "a": (730.0, 1090.0),
    "e": (530.0, 1840.0),
    "i": (270.0, 2290.0),
    "o": (570.0, 840.0),
    "u": (300.0, 870.0),
}

#: Each letter: the vowel it is said on, how it starts, and a pitch step of
#: its own in semitones — the reason two words have two different tunes.
LETTERS: Dict[str, Tuple[str, str, int]] = {
    "a": ("a", "", 0), "b": ("i", "stop", 2), "c": ("i", "fric", 2),
    "d": ("i", "stop", 1), "e": ("i", "", 3), "f": ("e", "fric", -1),
    "g": ("i", "stop", 1), "h": ("a", "fric", 0), "i": ("a", "", 4),
    "j": ("a", "stop", 2), "k": ("a", "stop", 0), "l": ("e", "liquid", -1),
    "m": ("e", "nasal", -2), "n": ("e", "nasal", -1), "o": ("o", "", -1),
    "p": ("i", "stop", 2), "q": ("u", "stop", -2), "r": ("a", "liquid", -2),
    "s": ("e", "fric", 1), "t": ("i", "stop", 3), "u": ("u", "", -3),
    "v": ("i", "fric", 1), "w": ("u", "liquid", -3), "x": ("e", "fric", 0),
    "y": ("a", "liquid", 1), "z": ("i", "fric", 2),
}

#: Pauses, in syllables.
SPACE_PAUSE = 0.6
CLAUSE_PAUSE = 1.8
SENTENCE_PAUSE = 2.6
LINE_PAUSE = 1.6

#: A syllable at 100% speed, seconds, and the bounds speed may push it to.
SYLLABLE_SECONDS = 0.055
SHORTEST = 0.026
LONGEST = 0.14

#: How far the voice may trail the text before it skims, seconds.
MAX_LAG = 1.0
#: How much sound may be written ahead of the clock, seconds.
MAX_LEAD = 0.12
#: Silence after which the device is let go, seconds.
IDLE_CLOSE = 3.0
#: Chunked sinks are handed this much at a time, seconds.
CHUNK_SECONDS = 0.3

#: The kinds of output that arrive as a stream — what MATCH STREAM paces.
STREAMED = frozenset({"answer", "thinking", "code"})
#: How far back the stream's pace is measured, seconds, and the shortest
#: span a measurement may claim: a burst that lands in one delta has no
#: duration of its own, and dividing by none would call it infinitely fast.
RATE_WINDOW = 1.5
RATE_FLOOR = 0.5

#: How long a tool call may stream before the tool voice starts talking over
#: it, seconds. A call written faster than this — a terminal command — sounds
#: exactly as it always did; a long one, a whole file in ``write_file``, is
#: not left silent while it streams.
SUSTAIN_GRACE = 0.4

#: What TEST says.
SAMPLE_LINE = "Hi! I'm your terminal — shall we get to work?"


def letter_for(char: str) -> Optional[str]:
    """The a–z letter a character is sounded as, or None for no sound.

    Accented Latin letters are their base letter; letters of every other
    script are given one by their code point, so a reply in Japanese or Greek
    babbles too rather than going silent.
    """
    if char.isdigit():
        return "abcdefghij"[int(char)] if char in "0123456789" else "o"
    if not char.isalpha():
        return None
    base = unicodedata.normalize("NFKD", char)[0].lower()
    if "a" <= base <= "z":
        return base
    return chr(ord("a") + ord(char) % 26)


# ── Plans ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Syllable:
    """One sound: a voiced blip, a tick, a hum."""

    hz: float
    seconds: float
    gain: float
    vowel: str = "a"
    #: ``""``, ``stop``, ``fric``, ``nasal`` or ``liquid``.
    onset: str = ""
    timbre: str = "round"
    breath: float = 0.0
    #: Pitch change across the sound; negative falls.
    glide: float = -0.06
    formant: float = 1.0


@dataclass(frozen=True)
class Rest:
    seconds: float


Sound = Union[Syllable, Rest]

#: A token queued for the voice: ("v", letter, variant), ("p", syllables of
#: pause, variant) or ("m", motif, variant).
Token = Tuple[str, Union[str, float], str]


def tokenize(text: str, variant: str) -> List[Token]:
    """Text, as the voice will take it: letters, pauses and inline motifs."""
    out: List[Token] = []
    for char in text:
        letter = letter_for(char)
        if letter is not None:
            out.append(("v", letter, variant))
            continue
        if char in " \t":
            pause = SPACE_PAUSE
        elif char == "\n":
            pause = LINE_PAUSE
        elif char in ",;:":
            pause = CLAUSE_PAUSE
        elif char in ".…":
            pause = SENTENCE_PAUSE
        elif char == "?":
            out.append(("m", "question", variant))
            pause = SENTENCE_PAUSE
        elif char == "!":
            out.append(("m", "exclaim", variant))
            pause = SENTENCE_PAUSE * 0.8
        else:
            continue
        if out and out[-1][0] == "p":
            # Runs of spaces and punctuation are one pause: the longest.
            previous = out[-1]
            out[-1] = ("p", max(float(previous[1]), pause), variant)
        else:
            out.append(("p", pause, variant))
    return out


def syllable_seconds(settings: ChatterSettings, voice: Voice, variant: Variant) -> float:
    """How long one syllable lasts for this voice, setting and kind of output."""
    pace = (settings.speed / 100.0) * voice.speed * variant.speed
    return max(SHORTEST, min(LONGEST, SYLLABLE_SECONDS / max(0.05, pace)))


def stream_pace(rate: float) -> Optional[Tuple[float, int]]:
    """MATCH STREAM: (seconds a syllable, letters it stands for) at ``rate``.

    ``rate`` is letters a second arriving from the model. A syllable lasts as
    long as a letter takes to arrive, within the bounds a syllable can be
    heard in; past the fastest syllable, each one stands for several letters,
    so the voice still finishes when the text does. None when there is no
    stream to follow.
    """
    if rate <= 0:
        return None
    seconds = max(SHORTEST, min(LONGEST, 1.0 / rate))
    return seconds, max(1, int(round(rate * seconds)))


def plan_letter(
    letter: str,
    settings: ChatterSettings,
    variant_name: str = "answer",
    rng: Optional[random.Random] = None,
    *,
    hurry: float = 1.0,
    seconds: Optional[float] = None,
) -> Syllable:
    """One letter as a syllable, in this voice, for this kind of output.

    ``seconds`` fixes how long it lasts — MATCH STREAM's pace — in place of
    the voice's own.
    """
    rng = rng or random.Random(0)
    voice = get_voice(settings.voice)
    variant = VARIANTS.get(variant_name, VARIANTS["answer"])
    vowel, onset, step = LETTERS.get(letter, ("a", "", 0))
    wander = (rng.random() * 2.0 - 1.0) * 2.5 * voice.wobble * variant.wobble * (settings.wobble / 100.0)
    semitones = settings.pitch + variant.semitones + step * 0.6 + wander
    base = seconds if seconds is not None else syllable_seconds(settings, voice, variant) * hurry
    seconds = base * (0.88 + 0.12 * rng.random())
    timbre = variant.timbre or voice.timbre
    if timbre == "tick":
        seconds = min(seconds, 0.022)
    return Syllable(
        hz=voice.hz * 2.0 ** (semitones / 12.0),
        seconds=max(0.012, seconds),
        gain=variant.gain * (0.86 + 0.14 * rng.random()),
        vowel=vowel,
        onset=onset,
        timbre=timbre,
        breath=variant.breath,
        glide=-0.06 + (rng.random() - 0.5) * 0.04,
        formant=voice.formant,
    )


def plan_motif(kind: str, settings: ChatterSettings) -> List[Sound]:
    """An event sound — a tool finishing, an error, a question's lift…"""
    voice = get_voice(settings.voice)
    sounds: List[Sound] = []
    for semitones, seconds, gain, glide, timbre in MOTIFS.get(kind, ()):
        sounds.append(
            Syllable(
                hz=voice.hz * 2.0 ** ((settings.pitch + semitones) / 12.0),
                seconds=seconds * (100.0 / max(50, settings.speed)) ** 0.5,
                gain=gain,
                vowel="u" if kind in {"wait", "compaction"} else "o",
                onset="nasal" if kind in {"wait", "compaction"} else "",
                timbre=timbre or voice.timbre,
                glide=glide,
                formant=voice.formant,
            )
        )
        sounds.append(Rest(0.02))
    return sounds


def plan_text(text: str, settings: ChatterSettings, variant: str = "answer",
              rng: Optional[random.Random] = None) -> List[Sound]:
    """All of ``text`` as sounds, at the voice's pace — no skimming.

    What :class:`Chatterbox` does to a stream one token at a time, done to a
    whole string at once: for TEST, and for anything that wants to know what
    a line will sound like.
    """
    rng = rng or random.Random(0)
    voice = get_voice(settings.voice)
    sounds: List[Sound] = []
    for kind, value, name in tokenize(text, variant):
        variant_obj = VARIANTS.get(name, VARIANTS["answer"])
        if kind == "v":
            sounds.append(plan_letter(str(value), settings, name, rng))
        elif kind == "p":
            sounds.append(Rest(float(value) * syllable_seconds(settings, voice, variant_obj)))
        else:
            sounds.extend(plan_motif(str(value), settings))
    return sounds


# ── Synthesis ────────────────────────────────────────────────────────────

_TABLE_SIZE = 512
_tables: Dict[tuple, List[float]] = {}
_tables_lock = threading.Lock()


def _harmonic_weight(timbre: str, h: int) -> float:
    if timbre == "chip":
        return 1.0 / h if h % 2 else 0.0
    if timbre == "reed":
        return 1.0 / h
    return 1.0 / (h ** 1.6)


def wavetable(timbre: str, vowel: str, hz: float, formant: float = 1.0) -> List[float]:
    """One cycle of the voice on ``vowel`` near ``hz``, peak-normalised.

    The harmonics are weighted by the vowel's formants — the same pitch on
    "ah" and on "ee" are different waves — and band-limited below 5 kHz so
    nothing folds back above the sample rate. Cached by quarter-octave.
    """
    bucket = round(math.log2(max(hz, 40.0)) * 4.0) / 4.0
    key = (timbre, vowel, bucket, round(formant, 2))
    with _tables_lock:
        table = _tables.get(key)
    if table is not None:
        return table
    f0 = 2.0 ** bucket
    f1, f2 = VOWEL_FORMANTS.get(vowel, VOWEL_FORMANTS["a"])
    f1, f2 = f1 * formant, f2 * formant
    weights = []
    h = 1
    while h * f0 < 5000.0 and h <= 24:
        weight = _harmonic_weight(timbre, h)
        if weight and timbre != "chip":
            partial = h * f0
            weight *= (
                0.2
                + math.exp(-(((partial - f1) / 160.0) ** 2))
                + 0.8 * math.exp(-(((partial - f2) / 240.0) ** 2))
            )
        if weight:
            weights.append((h, weight))
        h += 1
    if not weights:
        weights = [(1, 1.0)]
    values = [
        sum(w * math.sin(2.0 * math.pi * h * i / _TABLE_SIZE) for h, w in weights)
        for i in range(_TABLE_SIZE)
    ]
    peak = max(abs(v) for v in values) or 1.0
    table = [v / peak for v in values]
    with _tables_lock:
        if len(_tables) > 256:
            _tables.clear()
        _tables[key] = table
    return table


#: Headroom: the loudest syllable at full volume peaks this far below clipping.
_HEADROOM = 0.72


def render(sound: Sound, volume: float = 1.0, rate: int = SAMPLE_RATE,
           rng: Optional[random.Random] = None) -> bytes:
    """A sound as signed 16-bit little-endian mono PCM."""
    if isinstance(sound, Rest):
        return bytes(2 * max(0, int(rate * sound.seconds)))
    rng = rng or random.Random(0)
    n = max(1, int(rate * sound.seconds))
    out = array("h", bytes(2 * n))
    amp = max(0.0, min(1.0, volume)) * max(0.0, min(1.25, sound.gain)) * _HEADROOM * 32767.0
    amp = min(amp, 32767.0)
    if amp < 1.0:
        return out.tobytes()

    if sound.timbre == "tick":
        # A typewriter: a click of noise over a high, short tone.
        table = wavetable("reed", "i", sound.hz * 2.0, sound.formant)
        step = sound.hz * 2.0 * _TABLE_SIZE / rate
        phase = 0.0
        for i in range(n):
            phase += step
            decay = (1.0 - i / n) ** 3
            noise = rng.random() * 2.0 - 1.0
            out[i] = int((table[int(phase) % _TABLE_SIZE] * 0.5 + noise * 0.5) * decay * amp)
    else:
        table = wavetable(sound.timbre, sound.vowel, sound.hz, sound.formant)
        step = sound.hz * _TABLE_SIZE / rate
        attack = max(1, int(rate * 0.004))
        release = max(1, int(n * 0.45))
        release_at = n - release
        onset = sound.onset
        burst = int(rate * (0.014 if onset == "fric" else 0.006)) if onset in ("fric", "stop") else 0
        muffled = int(n * 0.3) if onset in ("nasal", "liquid") else 0
        breath = max(0.0, min(1.0, sound.breath))
        glide = sound.glide
        phase = 0.0
        previous = 0.0
        for i in range(n):
            phase += step * (1.0 + glide * (i / n - 0.5))
            value = table[int(phase) % _TABLE_SIZE]
            if i < burst:
                white = rng.random() * 2.0 - 1.0
                if onset == "fric":
                    value = value * 0.2 + (white - previous) * 0.4
                else:
                    value = value * 0.5 + white * 0.5
                previous = white
            elif breath:
                value = value * (1.0 - breath) + (rng.random() * 2.0 - 1.0) * breath
            if i < muffled:
                value *= 0.55
            if i < attack:
                env = i / attack
            elif i >= release_at:
                env = ((n - i) / release) ** 2
            else:
                env = 1.0
            out[i] = int(value * env * amp)
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


#: How each vowel colours a board-speaker beep — the beeper has no formants,
#: so the vowel moves the pitch a little instead.
_BOARD_COLOUR = {"a": 1.0, "e": 1.12, "i": 1.26, "o": 0.9, "u": 0.8}


def board_beep(sound: Syllable) -> Tuple[int, float, float]:
    """A syllable as a beep: (Hz, seconds on, seconds off)."""
    hz = speakers.clamp_board_hz(sound.hz * _BOARD_COLOUR.get(sound.vowel, 1.0))
    if sound.timbre == "tick":
        return speakers.clamp_board_hz(sound.hz * 2.0), 0.006, max(0.0, sound.seconds - 0.006)
    # A breathy or soft sound is a shorter beep: the beeper has one volume,
    # so how long it sounds is the only loudness it has.
    duty = 0.78 if sound.breath < 0.15 and sound.gain >= 0.6 else 0.55
    return hz, sound.seconds * duty, sound.seconds * (1.0 - duty)


# ── The engine ───────────────────────────────────────────────────────────


class Chatterbox:
    """Takes text as it streams and babbles it, on a thread of its own.

    ``feed`` and ``chirp`` return at once. The thread owns the device: it
    opens it the first time there is something to say, and lets it go after
    :data:`IDLE_CLOSE` seconds of quiet, so a console that is not talking is
    not holding the sound card or the speaker.
    """

    def __init__(
        self,
        settings: Optional[ChatterSettings] = None,
        *,
        open_soundcard: Optional[Callable[[], Tuple[Optional[speakers.Sink], str]]] = None,
        open_board_speaker: Optional[Callable[[], Tuple[Optional[speakers.Beeper], str]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        seed: Optional[int] = None,
    ) -> None:
        self._settings = settings or ChatterSettings()
        self._open_soundcard = open_soundcard or speakers.open_soundcard
        self._open_board = open_board_speaker or speakers.open_board_speaker
        self._clock = clock
        self._sleep = sleep
        self._rng = random.Random(seed)
        self._cond = threading.Condition()
        self._tokens: Deque[Token] = deque()
        self._voiced = 0
        self._motifs: Deque[List[Sound]] = deque()
        self._thread: Optional[threading.Thread] = None
        self._stopping = False
        self._generation = 0
        self._sink: Optional[speakers.Sink] = None
        self._beeper: Optional[speakers.Beeper] = None
        self._kind = ""
        self._failure = ""
        self._play_until = 0.0
        self._last_sound = 0.0
        self._chunk = bytearray()
        self._backticks = 0
        self._in_code = False
        self._last_motif: Dict[str, float] = {}
        #: When letters of the stream arrived, and how many: (time, count).
        self._arrivals: Deque[Tuple[float, int]] = deque()
        #: The stream's pace, letters a second — the last one measured, kept
        #: so the end of a reply is said at the pace the rest of it came.
        self._stream_rate = 0.0
        #: A tool call being written: (its name, when the voice may start).
        self._sustain: Optional[Tuple[str, float]] = None
        self._sustain_words = 0
        #: Syllables sounded so far — for the settings pane and the tests.
        self.spoken = 0

    # ── State ────────────────────────────────────────────────────────────

    @property
    def settings(self) -> ChatterSettings:
        return self._settings

    @property
    def failure(self) -> str:
        """Why the chatter went quiet on its own, or ""."""
        return self._failure

    @property
    def device(self) -> str:
        """What it is sounding through right now, or "" when nothing is open."""
        holder = self._beeper or self._sink
        return getattr(holder, "name", "") if holder is not None else ""

    @property
    def busy(self) -> bool:
        with self._cond:
            return bool(self._tokens or self._motifs)

    @property
    def stream_rate(self) -> float:
        """How fast the stream is arriving, letters a second (0: no stream)."""
        with self._cond:
            return self._stream_rate_locked()

    @property
    def sustaining(self) -> bool:
        """Whether a tool call is being written and the voice is on it."""
        return self._sustain is not None

    def apply(self, settings: ChatterSettings) -> None:
        """Adopt new settings. A failure is forgiven: the switch was thrown."""
        with self._cond:
            previous = self._settings
            self._settings = settings
            self._failure = ""
            if not settings.enabled:
                self._clear_locked()
                self._sustain = None
            if previous.board_speaker != settings.board_speaker or not settings.enabled:
                self._kind = "release"
            self._cond.notify_all()

    # ── Input ────────────────────────────────────────────────────────────

    def feed(self, text: str, variant: str = "answer") -> None:
        """Text as it streams. ``variant`` is what kind of output it is."""
        settings = self._settings
        if not text or not settings.enabled or self._failure:
            return
        if variant == "thinking" and not settings.thinking:
            return
        if variant == "answer":
            tokens = self._answer_tokens(text)
        else:
            tokens = tokenize(text, variant)
        if tokens:
            # Text after a tool call means the call is written: whatever the
            # tool voice was saying over it is over.
            self._sustain = None
            self._enqueue(tokens, streamed=variant in STREAMED)

    def sustain(self, tool: str) -> None:
        """A tool call has started streaming: keep the tool voice on it.

        After :data:`SUSTAIN_GRACE` seconds the tool voice reads the tool's
        name and goes on talking until the call is written — released by the
        call starting (a ``tool`` chirp), by any other output, or by
        :meth:`release`. A call that is written within the grace is never
        talked over, so a quick one sounds exactly as it did.
        """
        settings = self._settings
        if not settings.enabled or self._failure or not settings.tools:
            return
        with self._cond:
            if self._stopping:
                return
            self._sustain = (str(tool or "tool"), self._clock() + SUSTAIN_GRACE)
            self._sustain_words = 0
            self._start_locked()
            self._cond.notify_all()

    def release(self) -> None:
        """The tool call is written: the tool voice stops talking over it."""
        with self._cond:
            self._sustain = None
            self._cond.notify_all()

    def chirp(self, kind: str, text: str = "") -> None:
        """An event: ``tool`` (with its name), ``tool_done``, ``error``,
        ``wait`` or ``compaction``."""
        settings = self._settings
        if not settings.enabled or self._failure:
            return
        if kind != "wait":
            # Anything but a "still waiting" means the call being written is
            # done being written.
            self._sustain = None
        if kind in {"tool", "tool_done"} and not settings.tools:
            return
        spacing = MOTIF_SPACING.get(kind)
        if spacing is not None:
            now = self._clock()
            if now - self._last_motif.get(kind, -1e9) < spacing:
                return
            self._last_motif[kind] = now
        if kind == "tool":
            # The tool's name, read out in the tool voice: a few syllables of
            # it, never the whole of a long one.
            letters = [c for c in (text or "") if letter_for(c) is not None][:10]
            sounds: List[Sound] = [
                plan_letter(letter_for(c) or "a", settings, "tool", self._rng)
                for c in letters
            ]
            sounds.append(Rest(syllable_seconds(settings, get_voice(settings.voice), VARIANTS["tool"])))
        else:
            sounds = plan_motif(kind, settings)
        if sounds:
            self._enqueue_motif(sounds)

    def test(self, line: str = SAMPLE_LINE) -> None:
        """Say ``line`` now — whether or not the chatter is switched on."""
        self._failure = ""
        self._enqueue_motif(plan_text(line, self._settings, "answer", self._rng))

    def finish(self) -> None:
        """The turn is over: what is queued is said, and code is closed."""
        self._backticks = 0
        self._in_code = False
        with self._cond:
            self._sustain = None
            self._arrivals.clear()
            self._stream_rate = 0.0

    def hush(self) -> None:
        """Stop talking now, and forget everything queued."""
        with self._cond:
            self._clear_locked()
            self._sustain = None
            self._generation += 1
            sink = self._sink
            self._cond.notify_all()
        if sink is not None:
            try:
                sink.drop()
            except Exception:
                pass
        self.finish()

    def close(self) -> None:
        """Stop the thread and let the device go."""
        with self._cond:
            self._stopping = True
            self._clear_locked()
            self._generation += 1
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._release()

    # ── Queueing ─────────────────────────────────────────────────────────

    def _answer_tokens(self, text: str) -> List[Token]:
        """The answer's tokens, switching to the code voice inside fences.

        Fences arrive split across deltas as often as not, so backticks are
        counted across calls: three in a row, then anything else, flips it.
        """
        out: List[Token] = []
        run: List[str] = []

        def flush_run() -> None:
            if run:
                out.extend(tokenize("".join(run), "code" if self._in_code else "answer"))
                run.clear()

        for char in text:
            if char == "`":
                self._backticks += 1
                continue
            if self._backticks >= 3:
                flush_run()
                self._in_code = not self._in_code
            self._backticks = 0
            run.append(char)
        flush_run()
        return out

    def _enqueue(self, tokens: Sequence[Token], streamed: bool = False) -> None:
        with self._cond:
            if self._stopping:
                return
            voiced = sum(1 for token in tokens if token[0] == "v")
            self._tokens.extend(tokens)
            self._voiced += voiced
            if streamed and voiced:
                self._arrivals.append((self._clock(), voiced))
            self._start_locked()
            self._cond.notify_all()

    def _stream_rate_locked(self) -> float:
        """Letters a second over the last :data:`RATE_WINDOW` seconds."""
        now = self._clock()
        while self._arrivals and now - self._arrivals[0][0] > RATE_WINDOW:
            self._arrivals.popleft()
        if self._arrivals:
            letters = sum(count for _when, count in self._arrivals)
            span = max(RATE_FLOOR, now - self._arrivals[0][0])
            self._stream_rate = letters / span
        return self._stream_rate

    def _enqueue_motif(self, sounds: List[Sound]) -> None:
        with self._cond:
            if self._stopping:
                return
            self._motifs.append(sounds)
            self._start_locked()
            self._cond.notify_all()

    def _clear_locked(self) -> None:
        self._tokens.clear()
        self._motifs.clear()
        self._voiced = 0

    def _start_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="bench-ui-chatter", daemon=True
            )
            self._thread.start()

    # ── The thread ───────────────────────────────────────────────────────

    def _sustain_due_locked(self) -> bool:
        return self._sustain is not None and self._clock() >= self._sustain[1]

    def _sustain_word_locked(self) -> List[Sound]:
        """One word of the tool voice talking over a call being written.

        The first word is the tool's name — the same thing a quick call's
        chirp says — and the rest is the tool voice going on in its own
        babble, a word at a time, until the call is written.
        """
        settings = self._settings
        name = self._sustain[0] if self._sustain else "tool"
        if self._sustain_words == 0:
            letters = [letter_for(c) for c in name if letter_for(c) is not None][:10]
        else:
            letters = [
                chr(ord("a") + self._rng.randrange(26))
                for _ in range(self._rng.randint(2, 6))
            ]
        self._sustain_words += 1
        unit = syllable_seconds(settings, get_voice(settings.voice), VARIANTS["tool"])
        sounds: List[Sound] = [
            plan_letter(letter or "a", settings, "tool", self._rng) for letter in letters
        ]
        sounds.append(Rest(unit * (CLAUSE_PAUSE if self._sustain_words == 1 else SPACE_PAUSE * 1.5)))
        return sounds

    def _take_locked(self) -> List[Sound]:
        """The next thing to say: an event first, else the next of the text."""
        if self._motifs:
            return self._motifs.popleft()
        settings = self._settings
        voice = get_voice(settings.voice)
        if not self._tokens:
            return self._sustain_word_locked() if self._sustain_due_locked() else []
        newest = self._tokens[-1][2]
        unit = syllable_seconds(settings, voice, VARIANTS.get(newest, VARIANTS["answer"]))
        if settings.match_stream and newest in STREAMED:
            # Behind by the stream's pace, not the dial's: SPEED is set aside.
            pace = stream_pace(self._stream_rate_locked())
            if pace is not None:
                unit = pace[0] / pace[1]
        if self._voiced * unit > MAX_LAG:
            # More than a second behind: skip to the newest words, keeping
            # a little over half a second of them. Thinning the whole
            # backlog instead only ever approaches the text — each syllable
            # re-thins what is left — and a burst of a few hundred letters
            # was still being babbled seconds after it had appeared.
            keep = max(1, int(MAX_LAG * 0.6 / unit))
            while self._voiced > keep and self._tokens:
                if self._tokens.popleft()[0] == "v":
                    self._voiced -= 1
            while self._tokens and self._tokens[0][0] == "p":
                self._tokens.popleft()
        if not self._tokens:
            return []
        kind, value, variant = self._tokens.popleft()
        pace = (
            stream_pace(self._stream_rate_locked())
            if settings.match_stream and variant in STREAMED
            else None
        )
        if pace is not None:
            # MATCH STREAM: a syllable as often as letters arrive. Each one
            # stands for as many letters of the word as arrive in its time,
            # so the voice keeps the stream's pace instead of its own.
            unit, letters_each = pace
            if kind == "p":
                return [Rest(float(value) * unit)]
            if kind == "m":
                return plan_motif(str(value), settings)
            self._voiced -= 1
            for _ in range(letters_each - 1):
                if not self._tokens or self._tokens[0][0] != "v":
                    break
                self._tokens.popleft()
                self._voiced -= 1
            syllable = plan_letter(str(value), settings, variant, self._rng, seconds=unit)
            sounds: List[Sound] = [syllable]
            if unit - syllable.seconds > 0.002:
                # The rest of the letter's time — a tick is shorter than it.
                sounds.append(Rest(unit - syllable.seconds))
            return sounds
        unit = syllable_seconds(settings, voice, VARIANTS.get(variant, VARIANTS["answer"]))
        behind = self._voiced * unit > MAX_LAG * 0.5
        if kind == "p":
            # Behind the text, a pause is the first thing to shorten.
            return [Rest(float(value) * unit * (0.5 if behind else 1.0))]
        if kind == "m":
            return plan_motif(str(value), settings)
        self._voiced -= 1
        return [plan_letter(str(value), settings, variant, self._rng,
                            hurry=0.85 if behind else 1.0)]

    def _run(self) -> None:
        while True:
            release_now = False
            with self._cond:
                while (
                    not self._stopping
                    and not self._tokens
                    and not self._motifs
                    and not self._sustain_due_locked()
                ):
                    if self._kind == "release" or (
                        self._kind
                        and self._sustain is None
                        and self._clock() - self._last_sound > IDLE_CLOSE
                    ):
                        release_now = True
                        break
                    if (
                        not self._kind
                        and self._sustain is None
                        and self._clock() - self._last_sound > IDLE_CLOSE
                    ):
                        # Nothing open and nothing to say: the thread goes
                        # too, and the next thing fed starts another.
                        self._thread = None
                        return
                    timeout = 0.25
                    if self._sustain is not None:
                        timeout = max(0.01, min(timeout, self._sustain[1] - self._clock()))
                    self._cond.wait(timeout=timeout)
                if self._stopping:
                    break
                if release_now:
                    sounds: List[Sound] = []
                else:
                    sounds = self._take_locked()
                generation = self._generation
                pending = bool(self._tokens or self._motifs)
                kind = self._kind
            if release_now or kind == "release":
                self._flush_chunk()
                self._release()
                if release_now:
                    continue
            if sounds:
                try:
                    self._say(sounds, generation, pending)
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    self._fail(f"the chatter stopped: {type(exc).__name__}: {exc}")
        self._flush_chunk()
        self._release()

    def _open(self) -> bool:
        """Open the device the settings ask for. False (and a reason) if none."""
        board = self._settings.board_speaker
        want = "board" if board else "card"
        if self._kind == want and (self._sink is not None or self._beeper is not None):
            return True
        self._release()
        if board:
            beeper, why = self._open_board()
            if beeper is None:
                self._fail(why or "the board speaker is unavailable")
                return False
            self._beeper = beeper
        else:
            sink, why = self._open_soundcard()
            if sink is None:
                self._fail(why or "the sound card is unavailable")
                return False
            self._sink = sink
        self._kind = want
        self._play_until = self._clock()
        return True

    def _say(self, sounds: List[Sound], generation: int, pending: bool) -> None:
        if not self._open():
            return
        volume = self._settings.volume / 100.0
        for sound in sounds:
            if self._generation != generation or self._stopping:
                return
            if self._beeper is not None:
                self._beep(sound)
            elif self._sink is not None:
                self._play(render(sound, volume, rng=self._rng), sound)
            if isinstance(sound, Syllable):
                self.spoken += 1
            self._last_sound = self._clock()
        if not pending:
            self._flush_chunk()

    def _beep(self, sound: Sound) -> None:
        beeper = self._beeper
        if beeper is None:
            return
        if isinstance(sound, Rest):
            self._sleep(sound.seconds)
            return
        hz, on, off = board_beep(sound)
        beeper.play(hz, on, sleep=self._sleep)
        if off > 0:
            self._sleep(off)

    def _play(self, pcm: bytes, sound: Sound) -> None:
        sink = self._sink
        if sink is None:
            return
        seconds = len(pcm) / (2.0 * SAMPLE_RATE)
        if not sink.streaming:
            self._chunk.extend(pcm)
            if len(self._chunk) >= int(CHUNK_SECONDS * SAMPLE_RATE) * 2:
                self._flush_chunk()
            return
        now = self._clock()
        if self._play_until < now:
            self._play_until = now
        ahead = self._play_until - now
        if ahead > MAX_LEAD:
            self._sleep(ahead - MAX_LEAD)
        sink.write(pcm)
        self._play_until += seconds

    def _flush_chunk(self) -> None:
        sink = self._sink
        if not self._chunk or sink is None:
            self._chunk.clear()
            return
        pcm = bytes(self._chunk)
        self._chunk.clear()
        try:
            sink.write(pcm)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"{sink.name} stopped: {exc}")

    def _fail(self, why: str) -> None:
        with self._cond:
            self._failure = why
            self._clear_locked()
        self._release()

    def _release(self) -> None:
        sink, beeper = self._sink, self._beeper
        self._sink = None
        self._beeper = None
        self._chunk.clear()
        if self._kind == "release" or sink is not None or beeper is not None:
            self._kind = ""
        for holder in (beeper, sink):
            if holder is None:
                continue
            try:
                holder.close()
            except Exception:
                pass


__all__ = [
    "ChatterSettings",
    "Chatterbox",
    "DEFAULT_VOICE",
    "MAX_LAG",
    "MOTIFS",
    "RANGES",
    "Rest",
    "SAMPLE_LINE",
    "Syllable",
    "VARIANTS",
    "VOICES",
    "board_beep",
    "clamp_setting",
    "get_voice",
    "letter_for",
    "plan_letter",
    "plan_motif",
    "plan_text",
    "render",
    "stream_pace",
    "syllable_seconds",
    "tokenize",
    "wavetable",
]
