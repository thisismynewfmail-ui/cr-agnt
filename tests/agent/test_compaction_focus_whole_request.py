"""Compaction never hands the model a user's request cut off mid-sentence.

Reported from the bench console: after a compaction the model's thinking
began "the user's message got cut off mid-sentence … cut off at 'It must be
able…'", and it went searching the session history for the rest of a request
that was sitting, whole, right after the summary. The cut was the compactor's
own: the auto focus hint clipped each recent user turn to 260 characters with
a bare ellipsis (``text[:259].rstrip() + "…"``), quoted that to the
summarizer as the FOCUS TOPIC, and the summarizer quoted it on into the
summary. For the reported message the clip lands exactly on "…It must be
able…".
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.context_compressor import ContextCompressor

REQUEST = (
    "## Addition: Create This text editor inside the Desktop. Ensure ALL features "
    "are functional. This MUST scale correctly and function as intened. IT must "
    "also be the default software .txt files are opened into. MAKE SURE IT'S "
    "IN-THEME VIUSALLY! It must be able to be summoned through the right-click "
    "menu, and save documents/txt files into the folder it's opened into by "
    "default (e.g it opens the save name dialog when saving the file and naming "
    "it to the folder the editor was summoned from)."
)


def _focus(*user_texts: str) -> str:
    messages = []
    for text in user_texts:
        messages += [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
    return ContextCompressor._derive_auto_focus_topic(messages[:-1])


def _lines(focus: str) -> list[str]:
    return [line[2:] for line in focus.splitlines() if line.startswith("- ")]


def test_the_newest_request_reaches_the_focus_whole():
    focus = _focus("Earlier: check the PDA harness.", REQUEST)
    assert _lines(focus)[-1] == REQUEST
    assert "It must be able…" not in focus
    assert "[…]" not in focus


def test_older_turns_are_shortened_at_a_word_and_marked_as_the_hints_own():
    older = "Please refactor the scheduler " + "and keep the tick loop steady " * 20
    focus = _focus(older, REQUEST)
    first, newest = _lines(focus)
    assert newest == REQUEST
    assert first.endswith(" […]")
    # At a word, not mid-word, and nothing of it pretends to be the whole.
    body = first[: -len(" […]")]
    assert older.startswith(body) and older[len(body)] == " "
    assert "do not quote these lines as the user's words" in focus


def test_a_very_long_request_is_abridged_at_a_word_with_the_mark():
    huge = "Make the following changes carefully. " * 80
    newest = _lines(_focus(huge))[-1]
    assert newest.endswith(" […]")
    assert len(newest) <= 1400
    assert huge.startswith(newest[: -len(" […]")])


def test_the_total_cap_drops_older_turns_whole_instead_of_cutting_the_newest():
    older = ["Older request number %d about the logbook dropdown " % i * 6 for i in range(2)]
    focus = _focus(*older, REQUEST)
    lines = _lines(focus)
    assert lines[-1] == REQUEST
    for line in lines[:-1]:
        assert line.endswith(" […]") or line in older


@pytest.mark.parametrize("text", ["Refactor the auth module and add tests."])
def test_a_short_focus_is_unchanged(text):
    assert _focus(text) == f"Recent user focus:\n- {text}"


def test_the_summarizer_is_given_the_request_whole_in_a_real_compaction():
    """Through ``compress`` itself: what the summarizer is asked to preserve."""
    messages = [{"role": "system", "content": "You are Curie. " * 200}]
    for turn in range(10):
        messages.append({"role": "user", "content": f"Earlier request {turn}: fix part {turn}."})
        for call in range(3):
            call_id = f"c{turn}_{call}"
            messages.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps({"command": f"cat f{turn}{call}"})}}]})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": f"output {turn} {call} " * 500})
        messages.append({"role": "assistant", "content": f"Done with part {turn}."})
    messages.append({"role": "user", "content": REQUEST})

    asked = {}

    def summarizer(**kwargs):
        asked["prompt"] = kwargs["messages"][0]["content"]
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "## Active Task\nBuild the editor."
        response.choices[0].finish_reason = "stop"
        return response

    with patch("agent.context_compressor.get_model_context_length", return_value=180_000):
        compressor = ContextCompressor(model="local/model", quiet_mode=True,
                                       base_url="http://127.0.0.1:8080/v1", provider="custom")
    with patch("agent.context_compressor.call_llm", summarizer):
        compacted = compressor.compress(messages, current_tokens=170_000)

    assert REQUEST in asked["prompt"]
    assert "It must be able…" not in asked["prompt"]
    # And the request itself still follows the summary, whole.
    assert any(m.get("role") == "user" and m.get("content") == REQUEST for m in compacted)
