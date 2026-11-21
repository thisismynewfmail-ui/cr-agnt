"""Which area of the LOGBOOK a conversation belongs in.

The logbook listed every conversation in the store in one table, most recent
first. With a gateway running, that is not a record of the reader's work: a
scheduled task that fires every few minutes writes a conversation each time,
and a Telegram chat writes its own, so the conversations had on this bench
were buried under ones nobody typed here.

So the logbook is split into areas, chosen from a menu on the pane:

* **Chats** — conversations held at a keyboard on this machine: the bench
  itself (which runs as the ``cli`` surface), the classic CLI, the TUI, the
  desktop app and the other local surfaces. This is where the logbook opens.
* **Telegram** and **Scheduled** — always offered, because those are the two
  the reader asked to have kept apart, and an area that disappears when it is
  empty reads as a feature that went missing.
* **every other source the store holds** — each messaging platform the
  gateway serves (Discord, Slack, WhatsApp …) and any automated surface
  (Kanban workers, a plugin platform) gets an area of its own, so nothing is
  folded into Chats that was not a chat on this machine, and nothing is
  hidden: the menu lists every source that has a conversation in it.

The source a conversation is filed under is the ``source`` column the agent
writes when the session is created — the platform it ran on. Pure functions,
so the pane, the tests and anything else agree on the same filing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

#: The area keys that are not a single source.
AREA_CHATS = "chats"
AREA_SCHEDULED = "scheduled"
AREA_TELEGRAM = "telegram"

#: Local, interactive surfaces — a person at a keyboard on this machine. The
#: same set the desktop app keeps in its main list rather than giving each a
#: platform section (``LOCAL_SESSION_SOURCE_IDS`` there), plus the console's
#: own neighbours. ``""`` is a row written before sources were recorded; the
#: store itself counts those as ``cli``.
CHAT_SOURCES: Tuple[str, ...] = (
    "cli",
    "tui",
    "desktop",
    "webui",
    "acp",
    "local",
    "codex",
    "gateway",
    "",
)

#: The scheduler's runs. ``cron`` is the only source it writes: tasks made on
#: the SCHEDULE pane go through the same job store as ``curie cron``.
SCHEDULED_SOURCES: Tuple[str, ...] = ("cron",)

#: Offered whether or not anything is in them, in this order, first.
ALWAYS_OFFERED: Tuple[str, ...] = (AREA_CHATS, AREA_TELEGRAM, AREA_SCHEDULED)

#: How a source is named in the menu. Anything not listed is named after its
#: id, title-cased — a plugin platform still gets a readable entry.
SOURCE_LABELS: Dict[str, str] = {
    "api_server": "API server",
    "bluebubbles": "iMessage",
    "dingtalk": "DingTalk",
    "discord": "Discord",
    "email": "Email",
    "feishu": "Feishu",
    "homeassistant": "Home Assistant",
    "kanban": "Kanban",
    "matrix": "Matrix",
    "mattermost": "Mattermost",
    "msgraph_webhook": "Microsoft Graph",
    "photon": "Photon",
    "qqbot": "QQ",
    "relay": "Relay",
    "signal": "Signal",
    "slack": "Slack",
    "sms": "SMS",
    "telegram": "Telegram",
    "webhook": "Webhook",
    "wecom": "WeCom",
    "wecom_callback": "WeCom",
    "weixin": "WeChat",
    "whatsapp": "WhatsApp",
    "whatsapp_cloud": "WhatsApp",
    "yuanbao": "Yuanbao",
}

#: One line under the menu, saying what the chosen area holds.
_DESCRIPTIONS: Dict[str, str] = {
    AREA_CHATS: "conversations held on this machine — this bench, the CLI, "
    "the TUI and the desktop app",
    AREA_SCHEDULED: "the runs of scheduled tasks, one conversation per run",
}


@dataclass(frozen=True)
class Area:
    """One area of the logbook: what it is called and which sources it lists."""

    key: str
    label: str
    sources: Tuple[str, ...]
    count: int = 0

    @property
    def menu_label(self) -> str:
        """The area as the menu offers it: its name and how much is in it."""
        return f"{self.label} · {self.count:,}"

    @property
    def description(self) -> str:
        """One line saying what the area holds."""
        if self.key in _DESCRIPTIONS:
            return _DESCRIPTIONS[self.key]
        return f"conversations held through {self.label}"


def _normalise(source: Any) -> str:
    return str(source or "").strip().lower()


def source_label(source: Any) -> str:
    """A source's name as the menu shows it."""
    key = _normalise(source)
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    return key.replace("_", " ").replace("-", " ").title() or "Chats"


def area_of(source: Any) -> str:
    """The key of the area a conversation from ``source`` is listed in."""
    key = _normalise(source)
    if key in CHAT_SOURCES:
        return AREA_CHATS
    if key in SCHEDULED_SOURCES:
        return AREA_SCHEDULED
    return key


def _fixed_area(key: str, counts: Mapping[str, int]) -> Area:
    if key == AREA_CHATS:
        sources = CHAT_SOURCES
        label = "Chats"
    elif key == AREA_SCHEDULED:
        sources = SCHEDULED_SOURCES
        label = "Scheduled"
    else:
        sources = (key,)
        label = source_label(key)
    total = sum(int(counts.get(source, 0) or 0) for source in sources)
    return Area(key=key, label=label, sources=sources, count=total)


def logbook_areas(counts: Mapping[str, Any] | None) -> List[Area]:
    """Every area the menu offers, in the order it offers them.

    ``counts`` is ``{source: conversations}`` — the store's
    ``session_count_by_source``. The three fixed areas come first, then every
    other source with something in it, largest first (ties by name, so the
    menu does not reshuffle between two reads of the same store).
    """
    clean: Dict[str, int] = {}
    for source, count in (counts or {}).items():
        try:
            number = int(count or 0)
        except (TypeError, ValueError):
            continue
        key = _normalise(source)
        clean[key] = clean.get(key, 0) + number

    areas = [_fixed_area(key, clean) for key in ALWAYS_OFFERED]
    claimed = {source for area in areas for source in area.sources}
    others: Dict[str, int] = {}
    for source, count in clean.items():
        if count <= 0 or source in claimed:
            continue
        others[source] = others.get(source, 0) + count
    for source, _count in sorted(others.items(), key=lambda kv: (-kv[1], kv[0])):
        areas.append(_fixed_area(source, clean))
    return areas


def find_area(areas: Iterable[Area], key: str) -> Area | None:
    """The area with ``key``, or None when the menu does not offer it."""
    for area in areas:
        if area.key == key:
            return area
    return None


__all__ = [
    "ALWAYS_OFFERED",
    "AREA_CHATS",
    "AREA_SCHEDULED",
    "AREA_TELEGRAM",
    "Area",
    "CHAT_SOURCES",
    "SCHEDULED_SOURCES",
    "SOURCE_LABELS",
    "area_of",
    "find_area",
    "logbook_areas",
    "source_label",
]
