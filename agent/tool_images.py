"""Getting an image a tool returned in front of the model, on any chat server.

When the model looks at an image itself — ``vision_analyze`` on a file, a
screenshot from a browser tool — the image comes back as the *tool result*:
``[{type: text}, {type: image_url}]`` in a ``tool`` message. Anthropic, Gemini
and the big aggregators read images there. A plain OpenAI-compatible chat
server mostly does not: chat completions define a tool message's content as
text, and a local server (llama.cpp, LM Studio, vLLM, Ollama's compatibility
endpoint) either rejects the image part or silently drops it — and the model,
told the image is "loaded into your context", answers from nothing.

Every one of those servers reads images in a *user* turn. So on such a route
the request carries them there: each run of tool results keeps its text, and
the images it returned follow in one user turn directly after it, introduced
as what they are. This happens to the copy of the conversation being sent and
nowhere else — the stored history keeps each image in its own tool result —
and it is deterministic, so every later request carries the same turn in the
same place and the provider's prompt cache is unaffected. Role alternation
holds: ``assistant(tool_calls) → tool… → user → assistant`` is the ordinary
shape of a chat server's conversation.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Introduces the images the tool calls above returned. Says plainly that it
#: is not the reader speaking, so the model does not answer it as a request.
CARRIER_TEXT = (
    "[The image(s) returned by the tool call(s) above, attached here so you "
    "can see them directly. This is not a new request.]"
)

_IMAGE_TYPES = frozenset({"image_url", "input_image", "image"})


def _is_image_part(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") in _IMAGE_TYPES


def _text_of(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
        return str(part.get("text") or "")
    return ""


def carry_tool_images_in_user_turn(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The request with tool-result images moved into a user turn after them.

    Returns ``messages`` itself when no tool result carries an image, so the
    common case allocates nothing. Never mutates the dicts it is given: a
    message that changes is replaced by a new one.

    A run of tool results directly followed by a user message (the reader
    spoke before the model answered them) has its images put at the front of
    that message instead, so no two user turns stand together.
    """
    if not any(
        isinstance(m, dict)
        and m.get("role") == "tool"
        and isinstance(m.get("content"), list)
        and any(_is_image_part(p) for p in m["content"])
        for m in messages
    ):
        return messages

    out: List[Dict[str, Any]] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        if not (isinstance(message, dict) and message.get("role") == "tool"):
            out.append(message)
            index += 1
            continue
        images: List[Dict[str, Any]] = []
        while index < total:
            current = messages[index]
            if not (isinstance(current, dict) and current.get("role") == "tool"):
                break
            content = current.get("content")
            if isinstance(content, list) and any(_is_image_part(p) for p in content):
                found = [p for p in content if _is_image_part(p)]
                words = "\n".join(t for t in (_text_of(p) for p in content) if t).strip()
                noun = "image" if len(found) == 1 else f"{len(found)} images"
                note = f"[The {noun} this tool returned follows in the next message.]"
                replaced = dict(current)
                replaced["content"] = f"{words}\n\n{note}" if words else note
                out.append(replaced)
                images.extend(found)
            else:
                out.append(current)
            index += 1
        if not images:
            continue
        carried = [{"type": "text", "text": CARRIER_TEXT}, *images]
        following = messages[index] if index < total else None
        if isinstance(following, dict) and following.get("role") == "user":
            merged = dict(following)
            original = following.get("content")
            if isinstance(original, list):
                merged["content"] = [*carried, *original]
            else:
                merged["content"] = [*carried, {"type": "text", "text": str(original or "")}]
            out.append(merged)
            index += 1
        else:
            out.append({"role": "user", "content": carried})
    return out


def tool_images_need_user_turn(agent: Any) -> bool:
    """Whether the active route cannot read an image inside a tool result.

    True unless the provider is one known to accept media in tool results —
    and still true for one whose profile says it rejects list-type tool
    content, or that has rejected it already this session.
    """
    provider = str(getattr(agent, "provider", "") or "").strip()
    model = str(getattr(agent, "model", "") or "").strip()
    try:
        from tools.vision_tools import _supports_media_in_tool_results

        if not _supports_media_in_tool_results(provider, model):
            return True
    except Exception:
        return True
    try:
        if not agent._provider_supports_vision_tool_messages():
            return True
    except Exception:
        pass
    learned = getattr(agent, "_no_list_tool_content_models", None)
    if learned and (provider.lower(), model) in learned:
        return True
    return False


def carry_tool_images_for_chat(agent: Any, api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply :func:`carry_tool_images_in_user_turn` where the route needs it.

    Only for a model that sees (for one that does not, the images have
    already been replaced by descriptions) on a chat-completions route that
    cannot read images in tool results.
    """
    if getattr(agent, "api_mode", "chat_completions") not in (None, "", "chat_completions"):
        return api_messages
    try:
        if not agent._model_supports_vision():
            return api_messages
    except Exception:
        return api_messages
    if not tool_images_need_user_turn(agent):
        return api_messages
    return carry_tool_images_in_user_turn(api_messages)


__all__ = [
    "CARRIER_TEXT",
    "carry_tool_images_for_chat",
    "carry_tool_images_in_user_turn",
    "tool_images_need_user_turn",
]
