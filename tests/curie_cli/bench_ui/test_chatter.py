"""The chatter: an Animal Crossing–style voice for streaming text.

No audio hardware here or in CI, so the voice is held to what it produces:
the syllables it plans, the PCM it renders, the beeps it would send a board
speaker, and what it does to a fake device on a fake clock. The contracts
are the ones the feature promises — every letter a syllable, everything
around the answer lower than the answer, never far behind the stream,
silent when told — rather than any particular pitch or count.
"""

from __future__ import annotations

import asyncio
import os
import struct
import threading
import time
import wave
from array import array

import pytest

from curie_cli.bench_ui import speakers
from curie_cli.bench_ui.chatter import (
    MAX_LAG,
    MOTIFS,
    VARIANTS,
    VOICES,
    ChatterSettings,
    Chatterbox,
    Rest,
    Syllable,
    board_beep,
    letter_for,
    plan_letter,
    plan_motif,
    plan_text,
    render,
    stream_pace,
    syllable_seconds,
    tokenize,
)
from curie_cli.bench_ui.chatter import LONGEST, SHORTEST, SUSTAIN_GRACE  # noqa: E402

ON = ChatterSettings(enabled=True)
QUIET = ChatterSettings(enabled=True, wobble=0)


def _syllables(sounds):
    return [s for s in sounds if isinstance(s, Syllable)]


def _mean_hz(sounds):
    hz = [s.hz for s in _syllables(sounds)]
    return sum(hz) / len(hz)


# ── What a line becomes ──────────────────────────────────────────────────


def test_every_letter_is_a_syllable_and_the_gaps_are_pauses():
    sounds = plan_text("hi there, friend", ON)
    assert len(_syllables(sounds)) == len("hithere" + "friend")
    rests = [s for s in sounds if isinstance(s, Rest)]
    assert len(rests) == 2
    assert rests[1].seconds > rests[0].seconds, "a comma is a longer breath than a space"


def test_symbols_are_silent_and_runs_of_punctuation_are_one_pause():
    tokens = tokenize("**bold** ... ok", "answer")
    kinds = [kind for kind, _value, _variant in tokens]
    assert kinds.count("v") == len("boldok")
    assert kinds.count("p") == 1, "space, ellipsis, space: one pause, the longest"
    (pause,) = [value for kind, value, _variant in tokens if kind == "p"]
    assert pause == max(value for kind, value, _v in tokenize(".", "answer") if kind == "p")


@pytest.mark.parametrize("char, expected", [("a", "a"), ("Z", "z"), ("é", "e"), ("ß", None)])
def test_latin_letters_sound_as_themselves(char, expected):
    letter = letter_for(char)
    if expected is None:
        assert letter is not None and "a" <= letter <= "z"
    else:
        assert letter == expected


def test_every_script_babbles_and_symbols_do_not():
    for char in "日本語Ωжक7":
        letter = letter_for(char)
        assert letter is not None and "a" <= letter <= "z", char
    for char in "!?.,*#`-_ \n":
        assert letter_for(char) is None, repr(char)


@pytest.mark.parametrize("variant", sorted(set(VARIANTS) - {"answer"}))
def test_the_work_around_the_answer_is_lower_than_the_answer(variant):
    line = "the quick brown fox jumps over the lazy dog"
    assert _mean_hz(plan_text(line, ON, variant)) < _mean_hz(plan_text(line, ON, "answer"))


@pytest.mark.parametrize("kind", ["tool_done", "error", "wait", "compaction"])
def test_event_sounds_sit_below_the_voice(kind):
    voice = VOICES[ON.voice]
    assert all(s.hz < voice.hz for s in _syllables(plan_motif(kind, ON)))


def test_thinking_is_softer_breathier_and_slower_than_the_answer():
    answer = plan_text("thinking it over", ON, "answer")
    thinking = plan_text("thinking it over", ON, "thinking")
    assert max(s.gain for s in _syllables(thinking)) < min(s.gain for s in _syllables(answer))
    assert all(s.breath > 0 for s in _syllables(thinking))
    assert sum(s.seconds for s in thinking) > sum(s.seconds for s in answer)


def test_an_error_falls_and_a_question_lifts():
    assert all(s.glide < 0 for s in _syllables(plan_motif("error", ON)))
    question = _syllables(plan_motif("question", ON))
    assert question and all(s.glide > 0 for s in question)
    assert ("m", "question", "answer") in tokenize("really?", "answer")
    assert ("m", "exclaim", "answer") in tokenize("wow!", "answer")


def test_tone_moves_every_syllable_by_semitones():
    low = plan_letter("a", QUIET)
    octave_up = plan_letter("a", ChatterSettings(enabled=True, wobble=0, pitch=12))
    assert octave_up.hz == pytest.approx(low.hz * 2, rel=1e-6)


def test_without_wobble_a_letter_always_has_the_same_pitch():
    first = plan_letter("k", QUIET)
    again = plan_letter("k", QUIET, rng=__import__("random").Random(99))
    assert first.hz == pytest.approx(again.hz)


def test_speed_shortens_syllables_within_bounds():
    voice, answer = VOICES["sweet"], VARIANTS["answer"]
    slow = syllable_seconds(ChatterSettings(speed=50), voice, answer)
    fast = syllable_seconds(ChatterSettings(speed=200), voice, answer)
    assert slow > fast
    for speed in (50, 200):
        for name in VOICES:
            for variant in VARIANTS.values():
                seconds = syllable_seconds(ChatterSettings(speed=speed), VOICES[name], variant)
                assert 0.02 <= seconds <= 0.15


def test_every_voice_has_its_own_pitch():
    assert len({voice.hz for voice in VOICES.values()}) == len(VOICES)


# ── What it sounds like ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(VOICES))
def test_a_syllable_lasts_as_planned_and_never_clips(name):
    settings = ChatterSettings(enabled=True, voice=name, volume=100)
    for variant in VARIANTS:
        syllable = plan_letter("s", settings, variant)
        pcm = render(syllable, volume=1.0)
        samples = array("h")
        samples.frombytes(pcm)
        assert len(samples) == int(speakers.SAMPLE_RATE * syllable.seconds)
        peak = max(abs(v) for v in samples)
        assert 0 < peak <= 32767


def test_volume_scales_the_sound_and_zero_is_silence():
    syllable = plan_letter("a", QUIET)
    loud, soft, mute = (array("h", render(syllable, v)) for v in (1.0, 0.3, 0.0))
    assert max(map(abs, soft)) < max(map(abs, loud))
    assert not any(mute)


def test_a_rest_is_silence_of_its_length():
    pcm = render(Rest(0.1))
    assert len(pcm) == 2 * int(speakers.SAMPLE_RATE * 0.1)
    assert not any(pcm)


def test_a_board_beep_stays_in_the_speakers_range():
    for variant in VARIANTS:
        for letter in "aeiouxyz":
            hz, on, off = board_beep(plan_letter(letter, ON, variant))
            assert speakers.BOARD_MIN_HZ <= hz <= speakers.BOARD_MAX_HZ
            assert on > 0 and off >= 0


def test_a_soft_sound_is_a_shorter_beep_on_a_speaker_with_one_volume():
    answer = plan_letter("a", QUIET, "answer")
    thinking = plan_letter("a", QUIET, "thinking")
    _, on_answer, _ = board_beep(answer)
    _, on_thinking, _ = board_beep(thinking)
    assert on_thinking / thinking.seconds < on_answer / answer.seconds


# ── The engine, on fake devices and a fake clock ─────────────────────────


class _Clock:
    """Time that passes only when the engine sleeps — instantly."""

    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        seconds = max(0.0, seconds)
        self.now += seconds
        self.slept += seconds
        time.sleep(0)


class _Sink(speakers.Sink):
    name = "a fake sound card"
    streaming = True

    def __init__(self):
        self.pcm = bytearray()
        self.closed = False
        self.dropped = 0

    def write(self, pcm):
        self.pcm.extend(pcm)

    def drop(self):
        self.dropped += 1

    def close(self):
        self.closed = True

    @property
    def seconds(self):
        return len(self.pcm) / (2.0 * speakers.SAMPLE_RATE)


class _Beeper(speakers.Beeper):
    name = "a fake board speaker"

    def __init__(self):
        self.tones: list = []
        self.beeps: list = []
        self.closed = False

    def tone(self, hz):
        self.tones.append(hz)

    def play(self, hz, seconds, sleep=time.sleep):
        self.beeps.append((hz, seconds))
        super().play(hz, seconds, sleep=sleep)

    def close(self):
        self.tones.append(0)
        self.closed = True


def _box(settings=ON, sink=None, beeper=None, **kwargs):
    clock = _Clock()
    sink = sink if sink is not None else _Sink()
    beeper = beeper if beeper is not None else _Beeper()
    box = Chatterbox(
        settings,
        open_soundcard=lambda: (sink, ""),
        open_board_speaker=lambda: (beeper, ""),
        clock=clock,
        sleep=clock.sleep,
        seed=7,
        **kwargs,
    )
    return box, sink, beeper, clock


def _settle(box, timeout=10.0):
    deadline = time.monotonic() + timeout
    while box.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)


def test_streamed_text_is_spoken():
    box, sink, _beeper, _clock = _box()
    try:
        for piece in ("hel", "lo th", "ere"):
            box.feed(piece)
        _settle(box)
        assert box.spoken == len("hellothere")
        assert sink.seconds > 0.3
    finally:
        box.close()


def test_a_burst_too_fast_to_read_skips_ahead_rather_than_lagging():
    """Six hundred letters at once is a burst no voice could read out: the
    voice jumps to the newest words and is done within about a second."""
    box, sink, _beeper, _clock = _box()
    try:
        box.feed("abcdefghij" * 60)
        _settle(box)
        unit = syllable_seconds(ON, VOICES[ON.voice], VARIANTS["answer"])
        assert box.spoken <= int(MAX_LAG / unit) + 2
        assert sink.seconds <= MAX_LAG * 1.5
    finally:
        box.close()


def test_the_voice_never_runs_far_ahead_of_the_clock():
    """Sound is written just ahead of when it plays, so HUSH and new text
    take effect at once instead of after a long pre-buffered tail."""
    box, sink, _beeper, clock = _box()
    try:
        box.feed("hello there, this is a longer line of chatter")
        _settle(box)
        assert clock.slept > 0, "the stream was written without pacing"
        assert sink.seconds - clock.slept <= 0.12 + 0.2
    finally:
        box.close()


class _GatedSink(_Sink):
    """Holds the voice inside its first write until the gate opens."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.gate = threading.Event()

    def write(self, pcm):
        self.entered.set()
        self.gate.wait(timeout=5)
        super().write(pcm)


def test_hush_stops_mid_sentence_and_forgets_the_rest():
    sink = _GatedSink()
    box, sink, _beeper, _clock = _box(sink=sink)
    try:
        box.feed("a" * 15)
        assert sink.entered.wait(timeout=5), "the voice never started"
        box.hush()
        sink.gate.set()
        _settle(box)
        assert box.spoken <= 2, "the sentence carried on after HUSH"
        assert not box.busy
        assert sink.dropped >= 1, "sound already handed to the device was kept"
    finally:
        sink.gate.set()
        box.close()


def test_off_means_silent():
    box, sink, _beeper, _clock = _box(ChatterSettings(enabled=False))
    try:
        box.feed("hello")
        box.chirp("error")
        _settle(box)
        assert box.spoken == 0 and not sink.pcm
    finally:
        box.close()


def test_thinking_and_tools_can_each_be_silenced():
    box, sink, _beeper, _clock = _box(
        ChatterSettings(enabled=True, thinking=False, tools=False)
    )
    try:
        box.feed("considering", "thinking")
        box.chirp("tool", "read_file")
        box.chirp("tool_done")
        _settle(box)
        assert box.spoken == 0
        box.feed("ok", "answer")
        _settle(box)
        assert box.spoken == 2
    finally:
        box.close()


def test_a_tool_reads_out_a_few_letters_of_its_name():
    box, _sink, _beeper, _clock = _box()
    try:
        box.chirp("tool", "read_file")
        _settle(box)
        assert box.spoken == len("readfile")
        box.chirp("tool", "an_extremely_long_tool_name_indeed")
        _settle(box)
        assert box.spoken - len("readfile") <= 10
    finally:
        box.close()


def test_repeated_waits_are_one_sound():
    box, _sink, _beeper, _clock = _box()
    try:
        for _ in range(5):
            box.chirp("wait")
        _settle(box)
        assert box.spoken == len(MOTIFS["wait"])
    finally:
        box.close()


def test_a_device_that_will_not_open_falls_silent_and_says_why():
    box = Chatterbox(ON, open_soundcard=lambda: (None, "no player here"))
    try:
        box.feed("hello")
        _settle(box)
        deadline = time.monotonic() + 5
        while not box.failure and time.monotonic() < deadline:
            time.sleep(0.01)
        assert box.failure == "no player here"
        box.feed("more")
        assert not box.busy, "a failed chatter kept queueing"
        box.apply(ON)
        assert box.failure == "", "throwing a switch is a second try"
    finally:
        box.close()


def test_the_board_speaker_beeps_and_is_always_left_silent():
    settings = ChatterSettings(enabled=True, board_speaker=True)
    box, sink, beeper, _clock = _box(settings)
    try:
        box.feed("hey")
        _settle(box)
        assert len(beeper.beeps) == 3
        assert not sink.pcm, "the board speaker setting also played the sound card"
        assert beeper.tones[-1] == 0, "the speaker was left sounding"
    finally:
        box.close()
    assert beeper.closed and beeper.tones[-1] == 0


def test_code_in_the_answer_is_a_typewriter():
    settings = ChatterSettings(enabled=True, board_speaker=True)
    box, _sink, beeper, _clock = _box(settings)
    try:
        for piece in ("``", "`py\nabc\n``", "`\ndef"):
            box.feed(piece)
        _settle(box)
        ticks = [on for _hz, on in beeper.beeps if on < 0.01]
        assert len(ticks) == len("pyabc"), beeper.beeps
        assert len(beeper.beeps) - len(ticks) == len("def")
    finally:
        box.close()


def test_switching_to_the_board_speaker_lets_the_sound_card_go():
    box, sink, beeper, _clock = _box()
    try:
        box.feed("hi")
        _settle(box)
        assert sink.pcm and not sink.closed
        box.apply(ChatterSettings(enabled=True, board_speaker=True))
        box.feed("hi")
        _settle(box)
        assert sink.closed
        assert beeper.beeps
    finally:
        box.close()


def test_a_quiet_voice_lets_its_device_go():
    box, sink, _beeper, clock = _box()
    try:
        box.feed("hi")
        _settle(box)
        clock.now += 10.0
        deadline = time.monotonic() + 5
        while not sink.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sink.closed
    finally:
        box.close()


def test_test_plays_even_while_switched_off():
    box, sink, _beeper, _clock = _box(ChatterSettings(enabled=False))
    try:
        box.test("hi")
        _settle(box)
        assert box.spoken == 2 and sink.pcm
    finally:
        box.close()


# ── MATCH STREAM ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("rate", [4.0, 12.0, 30.0, 80.0, 250.0])
def test_match_stream_keeps_up_with_any_stream(rate):
    """A syllable as often as letters arrive, within what can be heard: the
    voice consumes letters at least as fast as the stream delivers them."""
    seconds, letters_each = stream_pace(rate)
    assert SHORTEST <= seconds <= LONGEST
    assert letters_each / seconds >= rate * 0.75


def test_a_faster_stream_is_a_faster_voice():
    slow, _ = stream_pace(8.0)
    fast, _ = stream_pace(30.0)
    assert fast < slow
    assert stream_pace(0.0) is None, "no stream: the voice keeps its own pace"


def _burst_seconds(settings, letters):
    box, sink, _beeper, _clock = _box(settings)
    try:
        box.feed("a" * letters)
        _settle(box)
        return sink.seconds
    finally:
        box.close()


def test_with_match_stream_a_burst_is_said_in_the_time_it_took_to_arrive():
    """Off, the voice's own pace sets the length — more letters, longer.
    On, the stream's pace does: a burst is said over the time a burst is
    taken to have arrived in, however many letters it held."""
    off = ChatterSettings(enabled=True)
    on = ChatterSettings(enabled=True, match_stream=True)
    assert _burst_seconds(off, 16) > _burst_seconds(off, 8) * 1.6
    short, long_ = _burst_seconds(on, 8), _burst_seconds(on, 16)
    assert long_ == pytest.approx(short, rel=0.25)


def test_match_stream_ignores_the_speed_dial():
    slow = ChatterSettings(enabled=True, match_stream=True, speed=50)
    fast = ChatterSettings(enabled=True, match_stream=True, speed=200)
    assert _burst_seconds(slow, 12) == pytest.approx(_burst_seconds(fast, 12), rel=0.2)


def test_the_stream_rate_is_measured_from_what_arrives():
    box, _sink, _beeper, clock = _box(ChatterSettings(enabled=True, match_stream=True))
    box._start_locked = lambda: None  # measure only: nothing speaks
    try:
        box.feed("abcde")
        clock.now += 0.5
        box.feed("fghij")
        assert box.stream_rate == pytest.approx(10 / 0.5)
        clock.now += 1.0
        box.feed("klmnopqrst")
        assert box.stream_rate == pytest.approx(20 / 1.5)
        box.finish()
        assert box.stream_rate == 0.0, "a new turn starts without the old pace"
    finally:
        box.close()


# ── A tool call being written ────────────────────────────────────────────


def _until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_a_quick_tool_call_sounds_as_it_always_did():
    """A terminal command is written well inside the grace: the tool voice
    never starts over it, so the call is its name and nothing else."""
    box, _sink, _beeper, _clock = _box()
    try:
        box.sustain("terminal")
        box.chirp("tool", "terminal")
        _settle(box)
        time.sleep(0.3)
        assert box.spoken == len("terminal")
        assert not box.sustaining
    finally:
        box.close()


def test_a_long_tool_call_keeps_the_tool_voice_talking_while_it_streams():
    box, _sink, _beeper, clock = _box()
    try:
        box.sustain("write_file")
        time.sleep(0.1)
        assert box.spoken == 0, "the voice started before the grace was up"
        clock.now += SUSTAIN_GRACE + 0.1
        assert _until(lambda: box.spoken > len("writefile") + 3), (
            "the tool voice did not keep talking while the call was written"
        )
        box.chirp("tool", "write_file")  # the call starts running
        _settle(box)
        settled = box.spoken
        time.sleep(0.4)
        assert box.spoken == settled, "the tool voice went on after the call started"
    finally:
        box.close()


@pytest.mark.parametrize("ending", ["text", "done", "hush", "finish"])
def test_anything_else_ends_the_tool_voice(ending):
    box, _sink, _beeper, clock = _box()
    try:
        box.sustain("write_file")
        clock.now += SUSTAIN_GRACE + 0.1
        assert _until(lambda: box.spoken > 0)
        {
            "text": lambda: box.feed("Done."),
            "done": lambda: box.chirp("tool_done"),
            "hush": box.hush,
            "finish": box.finish,
        }[ending]()
        assert not box.sustaining
    finally:
        box.close()


def test_a_slow_provider_notice_does_not_cut_the_tool_voice():
    box, _sink, _beeper, clock = _box()
    try:
        box.sustain("write_file")
        box.chirp("wait")
        assert box.sustaining
    finally:
        box.close()


def test_with_tools_silenced_a_call_is_not_talked_over():
    box, _sink, _beeper, clock = _box(ChatterSettings(enabled=True, tools=False))
    try:
        box.sustain("write_file")
        clock.now += 2.0
        time.sleep(0.3)
        assert box.spoken == 0 and not box.sustaining
    finally:
        box.close()


# ── Reaching the hardware ────────────────────────────────────────────────


@pytest.mark.parametrize("player", speakers.PLAYERS)
def test_each_player_is_told_the_format_and_reads_stdin(player):
    argv = speakers.player_command(player, 22050)
    assert argv and argv[0] == player
    joined = " ".join(argv)
    assert "22050" in joined
    assert argv[-1] == "-" or "--raw" in argv


def test_linux_tries_the_pipe_players_before_portaudio():
    routes = speakers.soundcard_routes("linux")
    assert routes.index("sounddevice") > max(routes.index(p) for p in speakers.PLAYERS)
    assert "sounddevice" not in speakers.soundcard_routes("darwin")


def test_with_no_player_the_sound_card_says_what_to_install(monkeypatch):
    monkeypatch.setattr(speakers, "_has_module", lambda name: False)
    ok, why = speakers.describe_output(False, which=lambda _name: None, platform="linux")
    assert not ok and "paplay" in why


def test_the_player_it_would_use_is_named(monkeypatch):
    monkeypatch.setattr(speakers, "_has_module", lambda name: False)
    ok, where = speakers.describe_output(
        False, which=lambda name: "/usr/bin/aplay" if name == "aplay" else None,
        platform="linux",
    )
    assert ok and "aplay" in where


def test_the_pc_speaker_device_is_found(tmp_path):
    by_path = tmp_path / "by-path"
    by_path.mkdir()
    device = by_path / "platform-pcspkr-event-spk"
    device.write_text("")
    assert speakers.find_pcspkr(str(by_path), str(tmp_path / "none")) == str(device)


def test_the_pc_speaker_is_found_by_name_without_a_by_path_link(tmp_path):
    sysfs = tmp_path / "class" / "input" / "event7" / "device"
    sysfs.mkdir(parents=True)
    (sysfs / "name").write_text("PC Speaker\n")
    found = speakers.find_pcspkr(
        str(tmp_path / "missing"), str(tmp_path / "class" / "input"), "/dev/input"
    )
    assert found == "/dev/input/event7"


def test_a_board_speaker_it_cannot_write_to_is_explained(tmp_path):
    ok, why = speakers.describe_output(
        True, platform="linux",
        pcspkr=lambda: str(tmp_path / "event9-not-writable"), vt=lambda: None,
    )
    assert not ok and "cannot write" in why


def test_a_text_console_has_a_tone_even_without_the_device():
    ok, where = speakers.describe_output(
        True, platform="linux", pcspkr=lambda: None, vt=lambda: "/dev/tty3",
    )
    assert ok and "/dev/tty3" in where


def test_a_mac_has_no_board_speaker():
    ok, why = speakers.describe_output(True, platform="darwin")
    assert not ok and "Mac" in why


def test_only_a_virtual_terminal_counts_as_a_console(monkeypatch):
    monkeypatch.setattr(os, "ttyname", lambda fd: "/dev/pts/4")
    assert speakers.console_vt() is None
    monkeypatch.setattr(os, "ttyname", lambda fd: "/dev/tty2")
    assert speakers.console_vt() == "/dev/tty2"


@pytest.mark.linux_only
def test_the_pc_speaker_is_sent_tone_events_and_left_silent(tmp_path):
    device = tmp_path / "pcspkr"
    device.write_bytes(b"")
    beeper = speakers.EvdevBeeper(str(device))
    beeper.tone(440)
    beeper.close()
    size = struct.calcsize("llHHi")
    data = device.read_bytes()
    events = [struct.unpack("llHHi", data[i:i + size]) for i in range(0, len(data), size)]
    assert events[0][2:] == (speakers.EV_SND, speakers.SND_TONE, 440)
    assert events[-1][2:] == (speakers.EV_SND, speakers.SND_TONE, 0)


def test_chunks_are_valid_wav():
    import io

    pcm = render(plan_letter("a", ON))
    with wave.open(io.BytesIO(speakers.wav_bytes(pcm)), "rb") as clip:
        assert clip.getnchannels() == 1
        assert clip.getsampwidth() == 2
        assert clip.getframerate() == speakers.SAMPLE_RATE
        assert clip.readframes(clip.getnframes()) == pcm


# ── Settings ─────────────────────────────────────────────────────────────


def test_chatter_settings_are_clamped_and_defaulted():
    from curie_cli.bench_ui.settings import read_chatter

    parsed = read_chatter({
        "enabled": "yes", "voice": "PEPPY", "pitch": 99, "volume": -5,
        "speed": "fast", "wobble": 30.4, "thinking": "off",
    })
    assert parsed.enabled is True
    assert parsed.voice == "peppy"
    assert parsed.pitch == 12
    assert parsed.volume == 0
    assert parsed.speed == ChatterSettings().speed
    assert parsed.wobble == 30
    assert parsed.thinking is False
    assert read_chatter({"voice": "nobody"}).voice == ChatterSettings().voice
    assert read_chatter(None) == ChatterSettings()


def test_the_chatter_is_off_until_switched_on(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    from curie_cli.bench_ui.settings import read_settings

    assert read_settings().chatter.enabled is False


# ── On the console ───────────────────────────────────────────────────────

textual = pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import PanelPane, ToggleSwitch  # noqa: E402
from curie_cli.bench_ui.settings import read_settings  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


class _Recorder:
    """Stands in for the Chatterbox: what the console asked of it."""

    def __init__(self, settings):
        self.settings = settings
        self.calls: list = []
        self.failure = ""
        self.busy = False

    def apply(self, settings):
        self.settings = settings
        self.calls.append(("apply", settings.enabled))

    def feed(self, text, variant="answer"):
        self.calls.append(("feed", variant, text))

    def chirp(self, kind, text=""):
        self.calls.append(("chirp", kind))

    def sustain(self, tool):
        self.calls.append(("sustain", tool))

    def finish(self):
        self.calls.append(("finish",))

    def hush(self):
        self.calls.append(("hush",))

    def test(self, line=""):
        self.calls.append(("test",))

    def close(self):
        self.calls.append(("close",))


class _OpenTurn(_StubBridge):
    def submit(self, message):
        self.submitted.append(message)
        return True

    @property
    def busy(self):
        return True


@pytest.fixture
def console_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


async def _settle_ui(pilot, times=4):
    for _ in range(times):
        await pilot.pause()


def _console(bridge=None, *, chatter_on=True):
    app = BenchConsole(bridge=bridge or _StubBridge())
    settings = ChatterSettings(enabled=chatter_on)
    app._chatter_settings = settings
    app._chatter = _Recorder(settings)
    return app


def test_the_panel_switch_turns_it_on_and_remembers(console_home):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        app._chatter = _Recorder(app._chatter_settings)
        async with app.run_test(size=(150, 50)) as pilot:
            await _settle_ui(pilot)
            app.show_pane("panel")
            await _settle_ui(pilot)
            app.run_keyline_action("chatter")
            await _settle_ui(pilot)
            switch = app.query_one(PanelPane).query_one("#switch-chatter", ToggleSwitch)
            assert switch.is_on
            assert read_settings().chatter.enabled is True
            assert ("apply", True) in app._chatter.calls

    asyncio.run(scenario())


def test_the_dials_step_save_and_stop_at_their_ends(console_home):
    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 50)) as pilot:
            await _settle_ui(pilot)
            app.run_keyline_action("chatter-pitch-up")
            app.run_keyline_action("chatter-volume-down")
            await _settle_ui(pilot)
            saved = read_settings().chatter
            assert saved.pitch == 1
            assert saved.volume == ChatterSettings().volume - 10
            for _ in range(30):
                app.run_keyline_action("chatter-pitch-up")
            await _settle_ui(pilot)
            assert read_settings().chatter.pitch == 12
            assert ("test",) in app._chatter.calls, "a dial is heard as it is turned"

    asyncio.run(scenario())


def test_choosing_a_voice_saves_it(console_home):
    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 50)) as pilot:
            await _settle_ui(pilot)
            app._chatter_select_voice("gruff")
            await _settle_ui(pilot)
            assert read_settings().chatter.voice == "gruff"
            app._chatter_select_voice("nobody")
            assert read_settings().chatter.voice == "gruff"

    asyncio.run(scenario())


def test_the_stream_is_fed_to_the_voice_by_what_it_is(console_home):
    async def scenario():
        app = _console(_OpenTurn())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app._send("go")
            await _settle_ui(pilot)
            events = app.bridge._events
            events.put(TurnEvent("reasoning", "hidden thoughts"))
            app._pump_agent()
            app._bench().fold().collapsed = False
            await _settle_ui(pilot)
            events.put(TurnEvent("reasoning", "open thoughts"))
            events.put(TurnEvent("tool", "read_file"))
            events.put(TurnEvent("tool_done", "read_file"))
            events.put(TurnEvent("delta", "Here it is."))
            events.put(TurnEvent("error", "boom"))
            events.put(TurnEvent("done", "Here it is."))
            app._pump_agent()
            await _settle_ui(pilot)
            calls = app._chatter.calls
            assert ("feed", "thinking", "hidden thoughts") not in calls, (
                "thinking in a shut drawer was voiced"
            )
            assert ("feed", "thinking", "open thoughts") in calls
            assert ("chirp", "tool") in calls and ("chirp", "tool_done") in calls
            assert ("feed", "answer", "Here it is.") in calls
            assert ("chirp", "error") in calls
            assert ("finish",) in calls

    asyncio.run(scenario())


def test_stop_hushes_the_voice(console_home):
    async def scenario():
        app = _console(_OpenTurn())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app.run_keyline_action("stop")
            assert ("hush",) in app._chatter.calls

    asyncio.run(scenario())


def test_an_open_microphone_keeps_it_quiet(console_home):
    async def scenario():
        app = _console(_OpenTurn())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app.voice._listening = True
            app._send("go")
            app.bridge._events.put(TurnEvent("delta", "words"))
            app._pump_agent()
            await _settle_ui(pilot)
            assert not [c for c in app._chatter.calls if c[0] in {"feed", "chirp"}]

    asyncio.run(scenario())


def test_a_device_failure_is_reported_once(console_home):
    async def scenario():
        app = _console()
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app._chatter.failure = "the sound card went away"
            notices = []
            app._notify_panel = lambda text, seconds=8.0: notices.append(text)
            app._watch_chatter()
            app._watch_chatter()
            assert len(notices) == 1 and "went away" in notices[0]

    asyncio.run(scenario())


def test_the_console_closes_the_voice_on_the_way_out(console_home):
    holder = {}

    async def scenario():
        app = _console()
        holder["box"] = app._chatter
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)

    asyncio.run(scenario())
    assert ("close",) in holder["box"].calls


def test_a_real_chatterbox_is_closed_with_the_console(console_home):
    """The board speaker above all must never be left sounding."""
    beeper = _Beeper()

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        settings = ChatterSettings(enabled=True, board_speaker=True)
        app._chatter_settings = settings
        app._chatter = Chatterbox(
            settings, open_board_speaker=lambda: (beeper, ""), seed=1
        )
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app._chatter.feed("hi")
            for _ in range(40):
                await pilot.pause(0.05)
                if beeper.beeps:
                    break

    asyncio.run(scenario())
    assert beeper.beeps
    assert beeper.tones[-1] == 0
    assert not threading.enumerate() or all(
        t.name != "bench-ui-chatter" or not t.is_alive() for t in threading.enumerate()
    )


def test_a_tool_call_being_written_is_given_the_tool_voice(console_home):
    async def scenario():
        app = _console(_OpenTurn())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle_ui(pilot)
            app._send("save it")
            app.bridge._events.put(TurnEvent("tool_gen", "write_file"))
            app._pump_agent()
            await _settle_ui(pilot)
            assert ("sustain", "write_file") in app._chatter.calls

    asyncio.run(scenario())


def test_match_stream_sits_directly_above_speed_and_is_saved(console_home):
    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 50)) as pilot:
            await _settle_ui(pilot)
            panel = app.query_one(PanelPane)
            switch = panel.query_one("#switch-chatter-match", ToggleSwitch)
            speed_row = panel.query_one("#chatter-speed-down").parent
            siblings = list(switch.parent.children)
            assert siblings.index(speed_row) == siblings.index(switch) + 1
            app.run_keyline_action("chatter-match")
            await _settle_ui(pilot)
            assert switch.is_on
            assert read_settings().chatter.match_stream is True
            assert app._chatter.settings.match_stream is True

    asyncio.run(scenario())


def test_the_writing_switch_silences_tool_writing_and_waiting_on_the_model(console_home):
    """WRITING, below the other chatter settings, governs both long waits:
    a tool call being written (a file streaming into write_file) and the
    "waiting on <model>…" a slow provider sends. Tools and replies are not
    its business."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        app._chatter_settings = ChatterSettings(enabled=True)
        app._chatter = _Recorder(app._chatter_settings)
        async with app.run_test(size=(150, 50)) as pilot:
            await _settle_ui(pilot)
            app._chatter_sustain("write_file")
            app._chatter_chirp("wait")
            assert ("sustain", "write_file") in app._chatter.calls
            assert ("chirp", "wait") in app._chatter.calls

            app.show_pane("panel")
            await _settle_ui(pilot)
            app.run_keyline_action("chatter-writing")
            await _settle_ui(pilot)
            assert read_settings().chatter.writing is False
            switch = app.query_one(PanelPane).query_one("#switch-chatter-writing", ToggleSwitch)
            assert not switch.is_on

            app._chatter.calls.clear()
            app._chatter_sustain("write_file")
            app._chatter_chirp("wait")
            app._chatter_chirp("tool")
            app._chatter_feed("hello")
            kinds = [call[:2] for call in app._chatter.calls]
            assert ("sustain", "write_file") not in kinds
            assert ("chirp", "wait") not in kinds
            assert ("chirp", "tool") in kinds and ("feed", "answer") in kinds

    asyncio.run(scenario())


def test_writing_is_on_unless_written_down_off(console_home):
    assert read_settings().chatter.writing is True
    from curie_cli.bench_ui.settings import KEY_CHATTER_WRITING, write_setting

    write_setting(KEY_CHATTER_WRITING, False)
    assert read_settings().chatter.writing is False
