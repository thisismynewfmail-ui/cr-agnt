"""Loop Detect's eye: what counts as a model repeating itself, and what not.

Reported with three screenshots (``loopy_01``–``03``): a model's reasoning
writing the same paragraph — and the same block of paragraphs — word for
word, over and over, for as long as nobody stopped it. The passages below are
taken from those screenshots. The detector must catch each loop at the
moment its passage completes its third appearance, however the stream
happens to be cut into pieces, and must leave an honest answer alone.
"""

from __future__ import annotations

import random

import pytest

from agent.loop_detect import (
    DEFAULT_MESSAGE,
    LONG_LINE,
    RANGES,
    LoopDetector,
    LoopSettings,
    LoopWatch,
    clamp_loop_setting,
    read_loop_settings,
)

# ── The reported loops ───────────────────────────────────────────────────

# loopy_01: a block of four paragraphs going round.
ISSUE = (
    "OK I think the issue is: the error is from the `std` helper inside "
    "buildMats: `m.userData.worldUV=true; m.userData.meters=TEX[tex].userData.meters;` "
    "— TEX[tex] undefined → reading 'userData' of undefined! Which tex? MAT.door "
    "uses TEX.door... all defined. But wait, order: buildMats is called at boot, "
    "registerTex ran at parse time of part 01. All TEX entries defined then. "
    "Unless... the error is that MAT.stripe line references TEX.stripe — defined. Hmm."
)
STD = (
    "Actually — the `std` function reads TEX[tex].userData — for 'door', "
    "'logo_big', 'sign_warning', 'statue' those are registered with registerTex "
    "so fine. All std() calls: wall, wall_lower, floor, ceiling, metal, "
    "metal_dark, wood, stripe — all registered. So TEX[tex] should exist..."
)
UNLESS = "Unless registerTex failed silently? No, it would throw at parse time."
BOOT = (
    "Hmm, but the error happened at boot time. Let me think about what reads "
    "userData: 01_texgen registerTex line `t.userData.meters=...` — if "
    "THREE.CanvasTexture exists fine. 03 tileMeters. 03 buildMats? No."
)
LOOPY_01 = [ISSUE, STD, UNLESS, BOOT]

# loopy_02: the cycle is not exact — two near-copies of one paragraph take
# turns — but "Let me verify the geometry…" comes round word for word.
GEOMETRY = (
    "Let me verify the geometry one more time: the test walks to the enemy flag "
    "at (62, 0) for the blue player, which is the red flag's pad. The pickup "
    "fires when the red flag is within 3 units of the player, and that's exactly "
    "what happened — the system message confirmed the flag was grabbed."
)
SCORING = (
    "Now for scoring: the player carries the red flag back to their own pad at "
    "(-62, 0). The scoring check calculates the distance between the player's "
    "position and the red flag's original position (which is stored as the "
    "RED_FLAG constant at -62). When the player reaches that pad, the distance "
    "should be zero, triggering the score. The broadcast message should "
    "announce \"BLUE scores!\" — but it never arrived."
)
WONDERING = (
    "I'm wondering if the engine's position tracking is lagging behind the "
    "test's reported position. The engine clamps movement to 7.7 units per "
    "tick, and the test steps are 5 units, so they should be accepted."
)
FOUND_IT = (
    "Actually, I think I found it — the engine's position tracking might be "
    "lagging behind the test's reported position. The engine clamps movement "
    "to 7.7 units per tick, and the test steps are 5 units, so they should be "
    "accepted."
)
TRACE = "Let me trace through the actual code one more time to verify the scoring logic is correct."
CHECKING = "Checking the scoring condition..."
LOOPY_02 = [
    GEOMETRY, SCORING, WONDERING, TRACE, CHECKING,
    GEOMETRY, SCORING, FOUND_IT, TRACE, CHECKING,
    GEOMETRY, SCORING, WONDERING,
]

# loopy_03: one long paragraph, on its own, again and again.
DAEMON = (
    "The bot daemon runs as an asyncio task in the server process, managing bot "
    "state in memory with persona tags and presence tracking. It handles "
    "virtual presence (offline/online timers), social actions like commenting "
    "and friending through the database, and LLM text generation. When a real "
    "player joins an instance, the daemon spawns bot subprocesses for bots "
    "assigned to that world, sending join commands with user payloads and "
    "persona data. The bots then behave autonomously in the subprocess—moving, "
    "chatting, shooting—through a bot brain that simulates their actions. For "
    "the virtual layer, the daemon handles social activities like commenting, "
    "friending, and posting through the database, plus presence management and "
    "LLM calls. The LLM endpoint is currently down, so I need a fallback "
    "generator that produces plausible text locally using persona-conditioned "
    "templates, while still attempting the LLM first and respecting the "
    "configuration for the endpoint URL and context limits."
)


def _stream(detector, text, seed=0, largest=48):
    """Feed ``text`` in random pieces. Returns (characters fed, loop)."""
    rnd = random.Random(seed)
    position = 0
    while position < len(text):
        piece = text[position: position + rnd.randint(1, largest)]
        position += len(piece)
        found = detector.feed(piece)
        if found is not None:
            return position, found
    return position, None


def _end_of(text, passage, nth):
    """Where the newline after the ``nth`` appearance of ``passage`` ends."""
    at = -1
    for _ in range(nth):
        at = text.index(passage, at + 1)
    return text.index("\n", at + len(passage)) + 1


@pytest.mark.parametrize("seed", range(6))
def test_a_block_of_paragraphs_going_round_is_caught_on_its_third_pass(seed):
    text = "\n\n".join(LOOPY_01 * 5) + "\n\n"
    fed, loop = _stream(LoopDetector("thinking"), text, seed)
    assert loop is not None
    assert loop.count == 3 and loop.where == "thinking"
    assert loop.passage == ISSUE
    # Caught by the piece that finished the third appearance — not before
    # it, and not a paragraph later.
    finish = _end_of(text, ISSUE, 3)
    assert finish <= fed < finish + 48


@pytest.mark.parametrize("seed", range(6))
def test_a_loop_whose_cycle_varies_is_still_caught_by_what_repeats(seed):
    text = "\n\n".join(LOOPY_02) + "\n\n"
    fed, loop = _stream(LoopDetector("thinking"), text, seed)
    assert loop is not None and loop.passage == GEOMETRY
    assert fed >= _end_of(text, GEOMETRY, 3)


@pytest.mark.parametrize("seed", range(6))
def test_one_long_paragraph_going_round_is_caught(seed):
    text = "\n\n".join([DAEMON] * 4) + "\n\n"
    fed, loop = _stream(LoopDetector("thinking"), text, seed, largest=200)
    assert loop is not None and loop.passage == DAEMON
    assert _end_of(text, DAEMON, 3) <= fed


def test_two_appearances_are_not_a_loop():
    text = "\n\n".join(LOOPY_01 * 2) + "\n\n"
    assert _stream(LoopDetector("thinking"), text)[1] is None


def test_a_loop_that_never_starts_a_new_line_is_caught_at_its_sentences():
    """A model can loop inside one endless paragraph; it is cut at sentence ends."""
    sentence = "I need to check whether the scheduler picks up the new task file. "
    text = sentence * 40
    assert len(text) > LONG_LINE
    fed, loop = _stream(LoopDetector("answer"), text, largest=30)
    assert loop is not None and loop.where == "answer"
    assert loop.passage.startswith("I need to check whether the scheduler")
    assert fed < len(text)


def test_the_threshold_is_the_repeats_setting():
    text = "\n\n".join(LOOPY_01 * 6) + "\n\n"
    for repeats in (2, 3, 5):
        fed, loop = _stream(LoopDetector("thinking", repeats=repeats), text)
        assert loop.count == repeats
        assert _end_of(text, ISSUE, repeats) <= fed


def test_case_spacing_and_list_markers_are_set_aside():
    line = "the same thing said again in a slightly different dress"
    detector = LoopDetector("answer")
    assert detector.feed(f"- {line.capitalize()}\n") is None
    assert detector.feed(f"   {line.upper()}  \n") is None
    assert detector.feed(f"1. {line}\n") is not None


def test_reset_starts_the_count_again():
    detector = LoopDetector("thinking")
    line = UNLESS + "\n"
    detector.feed(line * 2)
    detector.reset()
    assert detector.feed(line * 2) is None
    assert detector.feed(line) is not None


# ── What is not a loop ───────────────────────────────────────────────────


def test_short_lines_never_count():
    detector = LoopDetector("thinking")
    assert detector.feed("Wait.\nLet me check.\nHmm.\n" * 20) is None
    assert detector.feed("- [x] done\n- [x] done\n- [x] done\n") is None


def test_min_words_is_the_bar_a_line_has_to_clear():
    line = "one two three four five six seven\n"  # seven words
    assert LoopDetector("answer", min_words=8).feed(line * 5) is None
    assert LoopDetector("answer", min_words=7).feed(line * 5) is not None


def test_code_never_counts():
    block = (
        "```python\n"
        + "    logger.info('processing the next item in the queue now', item)\n" * 6
        + "```\n"
    )
    assert LoopDetector("answer").feed(block) is None


def test_a_long_line_of_code_is_not_held_on_to():
    detector = LoopDetector("answer")
    detector.feed("```\n" + "x" * (LONG_LINE * 3))
    assert len(detector._pending) <= LONG_LINE
    # ...and the fence still closes where it should.
    detector.feed("\n```\n")
    line = "this prose line comes after the code block has closed\n"
    assert detector.feed(line * 3) is not None


@pytest.mark.parametrize("blob", ["word " * 20_000, "x" * 100_000])
def test_a_run_with_no_line_or_sentence_end_is_not_held_on_to(blob):
    """Held and re-scanned, a blob grew the work on every piece after it."""
    detector = LoopDetector("answer")
    for start in range(0, len(blob), 500):
        assert detector.feed(blob[start:start + 500]) is None
        assert len(detector._pending) <= LONG_LINE + 500
    # Prose after it is still read, and a loop in it still caught.
    line = "and then the model went back to saying the same thing again\n"
    assert detector.feed("\n" + line * 2) is None
    assert detector.feed(line) is not None


def test_table_rows_never_count():
    row = "| yes | yes | yes | no | no | yes | yes | no | supported everywhere |\n"
    assert LoopDetector("answer").feed("| a | b |\n|---|---|\n" + row * 5) is None


def test_an_honest_answer_is_left_alone():
    answer = (
        "Here is what changed and why.\n\n"
        "The scheduler now reads the task file on every tick, so a new task is "
        "picked up without a restart.\n\n"
        "The config loader keeps the defaults for any key that is missing, "
        "rather than failing on the first one.\n\n"
        "The tests cover both: a task added mid-run, and a config with half its "
        "keys removed.\n\n"
        "- Run `scripts/run_tests.sh tests/cron/` to check the scheduler.\n"
        "- Run `scripts/run_tests.sh tests/config/` to check the loader.\n\n"
        "Nothing else in the tree was touched by this change at all.\n"
    )
    assert _stream(LoopDetector("answer"), answer * 1)[1] is None


def test_the_thinking_and_the_answer_are_counted_apart():
    watch = LoopWatch(LoopSettings())
    line = UNLESS + "\n"
    assert watch.feed("reasoning", line * 2) is None
    assert watch.feed("delta", line * 2) is None
    assert watch.feed("reasoning", line).where == "thinking"


def test_only_streamed_text_is_watched():
    watch = LoopWatch(LoopSettings())
    line = UNLESS + "\n"
    for kind in ("tool", "status", "warn", "error", "done"):
        assert watch.feed(kind, line * 5) is None


# ── Settings ─────────────────────────────────────────────────────────────


def test_the_defaults_are_on_three_repeats_and_the_reported_message():
    settings = read_loop_settings(None)
    assert settings == LoopSettings()
    assert settings.enabled is True
    assert settings.repeats == 3
    assert settings.message == DEFAULT_MESSAGE == (
        "Loop detected, break the loop and continue exactly as you were"
    )


def test_settings_read_from_config_are_clamped_and_total():
    settings = read_loop_settings({
        "enabled": "off", "repeats": 999, "min_words": -4, "resumes": "two",
        "message": "  Stop looping   and carry on. ",
    })
    assert settings.enabled is False
    assert settings.repeats == RANGES["repeats"][1]
    assert settings.min_words == RANGES["min_words"][0]
    assert settings.resumes == LoopSettings().resumes
    assert settings.message == "Stop looping and carry on."
    assert read_loop_settings("nonsense") == LoopSettings()
    assert read_loop_settings({"message": ""}).message == DEFAULT_MESSAGE


def test_scheduled_runs_are_off_by_default_and_read_from_config():
    assert LoopSettings().scheduled is False
    assert read_loop_settings({"scheduled": "yes"}).scheduled is True
    assert read_loop_settings({"scheduled": "maybe"}).scheduled is False


@pytest.mark.parametrize("name", sorted(RANGES))
def test_every_numeric_setting_has_its_default_inside_its_range(name):
    low, high, step = RANGES[name]
    default = getattr(LoopSettings(), name)
    assert low <= default <= high and step >= 1
    assert clamp_loop_setting(name, default) == default
    assert clamp_loop_setting(name, True) == default
