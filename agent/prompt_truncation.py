"""Detect a local model server that silently cut a request short.

A provider that cannot fit a request should refuse it. Local servers often do
not: Ollama keeps the system message and the newest message and drops the
rest to fit its loaded window, LM Studio truncates the middle of the prompt,
and llama.cpp builds with context shift discard the oldest tokens. None of
them says so. The request "succeeds", and the reply is a model answering a
conversation it never saw — on a Curie request, whose system prompt and tool
schemas alone are tens of thousands of tokens, typically one that kept the
system prompt and nothing else. The model then reasons that "the user's
message is empty", replies with nothing (or with an offer to help), and the
turn ends in the middle of the task. The next request may fit and the model
is suddenly back, as if nothing happened.

That shape is visible in one place: the prompt size the server reports in
its usage. A server that read far fewer tokens than were sent truncated the
request. This module turns that comparison into a decision, and into an error
the agent loop already knows how to recover from — learn the window the
server really serves, compact the conversation to fit it, and ask again —
instead of a reply nobody should act on.

Deliberately narrow, because a false positive throws away a good reply:

* **local endpoints only** — loopback, LAN, container and Tailscale hosts
  (:func:`agent.model_metadata.is_local_endpoint`). Hosted APIs refuse an
  oversized request rather than truncating it, and some proxies report usage
  in ways (cache buckets, partial counts) this comparison must not second-
  guess;
* **a big request** — at least :data:`MIN_SENT_TOKENS` sent;
* **and one of two readings that estimation error cannot produce:**

  - *relative* — within one turn each request is the previous one plus what
    was appended (the model's call and its tool results). An honest server's
    count for a strict extension of a prompt can only grow; it must grow by
    at least :data:`MIN_GROWTH_SHARE` of what was appended (a floor low
    enough for even pathologically compressible output — a tool result of
    one repeated character). A count that stalled or *shrank* while
    :data:`MIN_APPENDED_TOKENS` or more were appended is a server at its
    window, dropping the oldest messages first — which is exactly the
    conversation's opening request. Ollama's own rule keeps the system
    prompt and the newest messages, so this is the common shape and the
    one a ratio test alone misses: the server still read most of the
    request, just not the part that says what the task is;
  - *absolute* — for a request with no earlier reading to compare against
    (a resumed conversation's first call), the server read under
    :data:`TRUNCATION_RATIO` of what was sent *and* at least
    :data:`MIN_MISSING_TOKENS` fewer. The local estimate is rough (characters
    over four) and overestimates only on unusually compressible text — long
    runs of one character can halve — so a reading under 40% is not
    estimation error.
"""

from __future__ import annotations

from typing import Optional

#: A server that read less than this share of the request truncated it.
TRUNCATION_RATIO = 0.4

#: Requests smaller than this are never judged: there is nothing large enough
#: in them for a server to have dropped, and the estimate is least reliable on
#: short texts.
MIN_SENT_TOKENS = 8_192

#: The least that must be missing, whatever the ratio.
MIN_MISSING_TOKENS = 6_144

#: The least a request must have grown since the previous one in the turn
#: for the relative reading to judge it.
MIN_APPENDED_TOKENS = 2_048

#: The least share of the appended estimate an honest server's count grows by.
MIN_GROWTH_SHARE = 0.1

#: The smallest window this module will ever report.
_MIN_WINDOW = 2_048


class ServerTruncatedPromptError(Exception):
    """A local server served a truncated prompt; the reply must be discarded.

    Shaped as a provider context-overflow error on purpose — a 400 whose
    message names the window — so the agent loop's existing overflow recovery
    handles it: shrink the working window, compact, retry, and give up
    honestly after the bounded number of attempts.

    ``session_only_window`` tells that recovery not to persist the window: it
    is an inference from one truncation (Ollama, for one, drops whole
    messages, so the server can serve more than it read), and the server can
    be reloaded with a larger window at any time. A local endpoint is probed
    again when the next session starts.
    """

    status_code = 400
    session_only_window = True

    def __init__(
        self,
        *,
        served_tokens: int,
        sent_tokens: int,
        window: int,
        configured_window: int = 0,
    ) -> None:
        self.served_tokens = int(served_tokens)
        self.sent_tokens = int(sent_tokens)
        self.window = int(window)
        self.configured_window = int(configured_window or 0)
        # The first sentence is what the error classifier and
        # ``parse_context_limit_from_error`` read: "context length" routes it
        # to overflow recovery, and "maximum context length is N" (no digit
        # grouping) is the window it learns. Nothing here may mention the
        # *output* cap, or it is mistaken for a max_tokens error.
        super().__init__(
            f"Context length exceeded: the local model server's maximum "
            f"context length is {self.window} tokens. It silently truncated "
            f"this request, reading {self.served_tokens:,} of the "
            f"~{self.sent_tokens:,} prompt tokens sent, so its reply was "
            f"discarded."
        )


def _stalled(served: int, previous: Optional[int], appended: int) -> bool:
    """The relative reading: a strict extension whose count did not grow."""
    if previous is None or previous <= 0 or appended < MIN_APPENDED_TOKENS:
        return False
    return served - previous < appended * MIN_GROWTH_SHARE


def _far_short(served: int, sent: int) -> bool:
    """The absolute reading: far fewer tokens read than were sent."""
    return served < sent * TRUNCATION_RATIO and sent - served >= MIN_MISSING_TOKENS


def truncation_window(
    *,
    reported_prompt_tokens: int,
    sent_tokens: int,
    base_url: str,
    provider: str = "",
    max_tokens: Optional[int] = None,
    previous_prompt_tokens: Optional[int] = None,
    appended_tokens: int = 0,
) -> Optional[int]:
    """The window a server truncated a request to, or ``None`` if it did not.

    ``reported_prompt_tokens`` is the server's own count for the request,
    ``sent_tokens`` the local estimate of what was sent (messages, system
    prompt and tool schemas). ``previous_prompt_tokens`` is the server's
    count for the previous request of the same turn *when this request is a
    strict extension of it*, and ``appended_tokens`` the estimate of what was
    appended since (``None`` / ``0`` when there is no such request). Returns
    the context window the server evidently serves — what it read, plus room
    for a reply — when the conditions in the module docstring hold.
    """
    try:
        served = int(reported_prompt_tokens or 0)
        sent = int(sent_tokens or 0)
        previous = int(previous_prompt_tokens) if previous_prompt_tokens else None
        appended = int(appended_tokens or 0)
    except (TypeError, ValueError):
        return None
    if served <= 0 or sent < MIN_SENT_TOKENS:
        return None
    if not (_stalled(served, previous, appended) or _far_short(served, sent)):
        return None
    if str(provider or "").strip().lower() == "moa":
        # A MoA turn's usage is the aggregator's plus its advisors'; it is not
        # one server's reading of one request.
        return None
    try:
        from agent.model_metadata import is_local_endpoint

        if not is_local_endpoint(str(base_url or "")):
            return None
    except Exception:
        return None
    # The server kept ``served`` tokens of prompt and still had to leave room
    # to answer, so its window is at least that and somewhat more. The reply
    # room is taken from the configured output cap where there is one, and
    # never counted as more than half of what was read: a window inferred
    # from a truncation should err small, because erring large truncates
    # again.
    reply_room = 0
    try:
        if max_tokens and int(max_tokens) > 0:
            reply_room = min(int(max_tokens), served // 2)
    except (TypeError, ValueError):
        reply_room = 0
    window = served + reply_room
    window -= window % 256
    return max(_MIN_WINDOW, window)


__all__ = [
    "MIN_APPENDED_TOKENS",
    "MIN_GROWTH_SHARE",
    "MIN_MISSING_TOKENS",
    "MIN_SENT_TOKENS",
    "ServerTruncatedPromptError",
    "TRUNCATION_RATIO",
    "truncation_window",
]
