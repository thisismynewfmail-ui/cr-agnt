"""The console's content panes, one per switch on the rail.

Each pane reads real state and says plainly what it found. Where a pane
cannot answer — no sessions database yet, no provider configured, a check
that needs the network — it says so rather than showing a placeholder that
looks like data.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Collapsible, DataTable, RichLog, Static, TextArea

from curie_cli.bench_ui import dos
from curie_cli.bench_ui.indicators import (
    THINKING,
    KitPreview,
    Pen,
    get_kit,
    kit_catalogue,
)
from curie_cli.bench_ui.settings import (
    list_piper_voices,
    piper_voices_dir,
    read_settings,
)


#: Textual reports the right mouse button as 3, matching the terminal's own
#: button numbering.
_RIGHT_BUTTON = 3


def _clipboard_text(widget) -> str:
    """Whatever the clipboard holds, as text.

    Tries the application's own clipboard first — Textual keeps one, fed by
    the terminal's bracketed-paste and by anything the app has copied — then
    falls back to the system clipboard through ``pyperclip`` when it is
    installed. Returns an empty string when neither can answer, because a
    paste that silently does nothing is better than a traceback over the
    interface.
    """
    try:
        held = getattr(widget.app, "clipboard", "")
        if isinstance(held, str) and held:
            return held
    except Exception:
        pass
    try:
        import pyperclip

        text = pyperclip.paste()
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


def _head(text: str) -> Static:
    return Static(text, classes="section-head")


def _note(text: str) -> Static:
    return Static(text, classes="note")


class Composer(TextArea):
    """The input area, with Enter bound to send rather than to newline.

    ``TextArea`` claims Enter for itself in ``_on_key`` before any app-level
    handler runs, which is right for an editor and wrong for a chat composer:
    the message could be typed but never sent. Intercepting it here — and
    only it — keeps every other editing key (arrows, Home/End, selection,
    undo) exactly as ``TextArea`` defines it.

    Newline is Shift+Enter where the terminal distinguishes it, and Ctrl+J
    everywhere. Ctrl+J is the fallback because it *is* the newline character
    (LF, 0x0A): every terminal sends it, with no modifier-reporting protocol
    needed.
    """

    class Submitted(Message):
        """The composer asked for its contents to be sent."""

    NEWLINE_KEYS = frozenset({"shift+enter", "ctrl+j"})

    #: ``TextArea`` actions this composer declines, because their keys belong
    #: to the console: F6 is select-line and F7 is select-all upstream, and
    #: both are function keys on the bench's key line. The app claims them
    #: with priority bindings, so leaving these enabled here would only mean
    #: the composer's own help still offered two keys it would never get.
    DECLINED_ACTIONS = frozenset({"select_line", "select_all"})

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # None, not False: None takes the binding out of consideration
        # entirely and lets the key bubble, where False would keep it listed
        # as a disabled command.
        if action in self.DECLINED_ACTIONS:
            return None
        return True

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        """Right-click pastes, the way a terminal's own context menu would.

        Terminals differ on what the right button does, and inside a
        full-screen application the terminal's own paste menu is not
        available — so the console has to offer it. Left and middle buttons
        keep their ordinary meaning.
        """
        if event.button != _RIGHT_BUTTON:
            return
        event.prevent_default()
        event.stop()
        self.focus()
        text = _clipboard_text(self)
        if text:
            self.insert(text)

    async def _on_key(self, event: events.Key) -> None:
        if event.key in self.NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted())
            return
        await super()._on_key(event)


# ``Pen`` — the fold's thinking indicator — now lives in
# :mod:`curie_cli.bench_ui.indicators`, with the rest of the animated state
# figures. It is re-exported here because that is where it is mounted, and
# because a fold and its indicator are read as one thing.


class Fold(Collapsible):
    """A turn's workings, shut, behind a heading and a thinking indicator.

    Reasoning and tool calls are how an answer got made, not the answer. Left
    inline they bury the reply they belong to — a minute of thinking is
    several screens of text the reader has to scroll past to find one
    paragraph. So they go in here, shut by default, and the heading says only
    what the reader needs from the outside: that work is running (the pen
    sweeps), and how much of it there was (the tool count, once it is done).

    Opening it shows the whole run in order, reasoning included. Nothing is
    hidden — it is just not in the way.
    """

    def __init__(self, on_grow=None, **kwargs) -> None:
        self._body = Static("", classes="fold-body")
        self._lines: list[tuple[str, str]] = []
        self._tools: list[str] = []
        # Called whenever the fold's contents change. An *expanded* fold that
        # reasoning is streaming into grows the transcript without any entry
        # being mounted, so nothing else would tell the transcript to follow —
        # which is exactly the case of "open the thinking and then have to
        # keep scrolling down by hand".
        self._on_grow = on_grow
        super().__init__(self._body, title="WORKINGS", collapsed=True, **kwargs)

    # ── Content ──────────────────────────────────────────────────────────

    def add_reasoning(self, text: str) -> None:
        # Reasoning arrives in chunks that are rarely line-aligned, so it
        # continues the open run rather than starting a paragraph per delta.
        if self._lines and self._lines[-1][0] == "reasoning":
            self._lines[-1] = ("reasoning", self._lines[-1][1] + text)
        else:
            self._lines.append(("reasoning", text))
        self._sync()

    def add_tool(self, name: str) -> None:
        self._tools.append(name)
        self._lines.append(("tool", name))
        self._sync()

    def restyle(self, palette) -> None:
        """Re-render under a different skin's colours."""
        self._palette = palette
        self._sync()

    def _sync(self) -> None:
        palette = getattr(self, "_palette", None) or _app_palette(self)
        dim = palette["dim"]
        label = palette["secondary"]
        rule = getattr(palette, "tool_prefix", "\u2502")

        body = Text()
        for index, (kind, text) in enumerate(self._lines):
            if index:
                body.append("\n")
            if kind == "tool":
                body.append(f"{rule} ", style=dim)
                body.append(text, style=f"bold {label}")
            else:
                body.append(text, style=dim)
        self._body.update(body)
        self._sync_heading(palette)
        if self._on_grow is not None and not self.collapsed:
            try:
                self._on_grow()
            except Exception:
                # The transcript is not mounted yet, or is going away. A fold
                # must not take the console down over a scroll position.
                pass

    def _sync_heading(self, palette) -> None:
        """Put the heading and its marker in the display mode's own lettering.

        Both halves have to be pushed, and the marker is the reason. Textual's
        collapsible builds its label from a symbol plus a title, and the title
        is a reactive with a no-change guard — so a mode switch that leaves the
        wording alone ("WORKINGS", with no tool calls yet) would swap the
        symbol into the widget and never repaint it. Setting the title keeps
        the widget's own state right for the next collapse; the explicit
        update is what makes the marker appear now.
        """
        collapsed_symbol, expanded_symbol = (
            (dos.FOLD_COLLAPSED, dos.FOLD_EXPANDED)
            if getattr(palette, "dos", False)
            else ("\u25b6", "\u25bc")
        )
        heading = self._title_text(palette)
        self._title.collapsed_symbol = collapsed_symbol
        self._title.expanded_symbol = expanded_symbol
        self.title = heading
        marker = collapsed_symbol if self.collapsed else expanded_symbol
        # A Rich ``Text`` rather than a string: ``Static.update`` reads content
        # markup out of a string, and a heading is not markup.
        self._title.update(Text(f"{marker} {heading}", no_wrap=True))

    def _title_text(self, palette=None) -> str:
        """The shut fold's heading.

        Deliberately not a preview. It carries no reasoning text and no
        character count: the pen beside it says whether work is running, and
        the tool count says how much of it there was. Anything more is asking
        the reader to follow scratch work they did not open.
        """
        if getattr(palette, "dos", False):
            return dos.fold_title(len(self._tools))
        if not self._tools:
            return "WORKINGS"
        return f"WORKINGS · {_plural(len(self._tools), 'tool call')}"

    def _watch_collapsed(self, collapsed: bool) -> None:  # noqa: D401
        super()._watch_collapsed(collapsed)
        # The marker in front of the heading is half of what "shut" looks
        # like, and it is drawn by this widget rather than by the stylesheet.
        try:
            self._sync_heading(getattr(self, "_palette", None) or _app_palette(self))
        except Exception:
            # Toggled before the fold is mounted, or during teardown. The
            # marker is cosmetic; a fold must not raise over one.
            pass

    @property
    def collected_nothing(self) -> bool:
        """Whether this fold has anything in it.

        Deliberately not called ``is_empty``: ``DOMNode`` already defines
        that property to mean "no displayed children", and this fold always
        has one — its body.
        """
        return not self._lines


class Transcript(VerticalScroll):
    """The conversation, held against its newest line.

    Following the tail is the one behaviour a chat surface cannot get wrong,
    and it is easy to get wrong, because "is the reader at the bottom?"
    cannot be answered from geometry that is one layout pass old. The
    previous test did exactly that — compare ``scroll_offset.y`` against
    ``max_scroll_y`` immediately before mounting the new entry — and a fast
    reply outran it: the offset fell further and further behind a maximum
    that had already grown, the comparison stopped being true, and following
    silently stopped for the rest of the turn. Expanding a fold broke it the
    same way, and worse, because that adds several screens in a single pass
    with no scroll event at all.

    So whether to follow is *remembered* rather than re-derived. It starts
    true, goes false the moment the reader scrolls away from the bottom, and
    comes back the moment they return to it — and the only thing measured
    against live geometry is that last question, asked inside the scroll
    watcher, where the numbers are current by construction. The scroll itself
    is deferred to after the refresh that the new content triggers, which is
    the first moment the true height exists.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        #: Whether new content pulls the view down with it.
        self._pinned = True
        #: Set while this widget is scrolling itself, so its own movement is
        #: not mistaken for the reader scrolling away.
        self._programmatic = False
        #: One deferred scroll at a time; a burst of deltas must not queue a
        #: hundred callbacks that all do the same thing.
        self._scroll_queued = False
        #: Confirming passes still owed. Some growth settles over more than
        #: one layout pass — an expanding fold mounts nothing and changes the
        #: height of what is already there, and that height is not final
        #: until the pass *after* the one that caused it.
        self._settle_passes = 0

    # ── Following ────────────────────────────────────────────────────────

    def follow(self) -> None:
        """Bring the newest line into view, if the reader has not left it."""
        if not self._pinned:
            return
        self._settle_passes = 1
        if self._scroll_queued:
            return
        self._scroll_queued = True
        self.call_after_refresh(self._to_end)

    def pin(self) -> None:
        """Follow again from here on, whatever the reader did before."""
        self._pinned = True
        self.follow()

    @property
    def following(self) -> bool:
        """Whether new content will pull the view down."""
        return self._pinned

    def _to_end(self) -> None:
        self._scroll_queued = False
        self._programmatic = True
        try:
            # Immediate, because this callback already runs after the layout
            # pass that the new content caused — deferring again would read
            # the same number one frame later and scroll one frame late.
            self.scroll_to(y=self.max_scroll_y, animate=False, immediate=True)
        finally:
            self._programmatic = False
        # One confirming pass, bounded, for the growth that is not finished
        # settling yet. A loop would be a repaint that never stops; a single
        # pass is what an expanded fold needs and no more.
        if self._settle_passes > 0 and self._pinned:
            self._settle_passes -= 1
            self._scroll_queued = True
            self.call_after_refresh(self._to_end)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._programmatic:
            return
        # The reader moved the view. One row of slack, so landing a hair
        # short of the bottom with the wheel still counts as being there.
        self._pinned = new_value >= self.max_scroll_y - 1

    def on_resize(self, event: events.Resize) -> None:
        # A resize rewraps everything above, which moves the newest line.
        self.follow()


class Entry(Horizontal):
    """One block of the conversation: a speaker in the gutter, text beside it.

    The name goes in a fixed column and the text runs alongside it — *every*
    line of the text, not merely the first. Written as a prefix on the first
    line it only looks parallel until something wraps or the model opens its
    answer with a blank line, at which point the reply lands underneath its
    own name and the turn reads as two unrelated blocks.

    Kind and text are kept rather than a finished renderable, so a skin
    change re-renders the conversation already on screen instead of leaving
    it in the previous palette.
    """

    #: The kinds that get a name in the gutter, and the mark that stands for
    #: each: (full name, narrow name, palette role). The name is a glyph plus
    #: a word — the glyph carries at a glance, the word carries when the
    #: skin's colours are close together. On a narrow window the word goes
    #: and the glyph stays, because six columns of name out of forty is a
    #: sixth of the reading width spent saying something the reader knows.
    MARKS = {
        "user": ("▶ YOU", "▶", "primary"),
        "reply": ("▮ CURIE", "▮", "accent"),
        "error": ("✗ FAULT", "✗", "error"),
    }

    @staticmethod
    def marks_for(palette) -> dict:
        """The speaker marks of whichever display mode is painting.

        The DOS set is not a restyling of this one, it is a different
        alphabet: ``✗`` is U+2717 and no machine running DOS could draw it,
        so the fault mark there is CP437's double exclamation. A mark the
        period could not have printed is the detail that gives the whole
        thing away.
        """
        return dos.ENTRY_MARKS if getattr(palette, "dos", False) else Entry.MARKS

    def __init__(self, kind: str, text: str, palette=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.kind = kind
        self.entry_text = text
        self._palette = palette
        self._gutter = Static("", classes="entry-gutter")
        self._body = Static("", classes="entry-body")
        self._compact = False
        self.add_class("entry")
        self.add_class(f"entry-{kind}")
        if kind == "kv":
            self.add_class("entry-pair")
        self._paint()

    def compose(self) -> ComposeResult:
        yield self._gutter
        yield self._body

    # ── Content ──────────────────────────────────────────────────────────

    def set_text(self, text: str) -> None:
        self.entry_text = text
        self._paint()

    def restyle(self, palette) -> None:
        self._palette = palette
        self._paint()

    def set_compact(self, compact: bool) -> None:
        """Drop the name to its glyph, for a window with no room for it."""
        if compact == self._compact:
            return
        self._compact = compact
        self.set_class(compact, "-compact")
        self._paint()

    def _paint(self) -> None:
        palette = self._palette or _app_palette(self)
        if self.kind == "kv":
            key, _, what = self.entry_text.partition("\t")
            self._gutter.update(
                Text(key, style=f"bold {palette['foreground']}", no_wrap=True,
                     overflow="ellipsis")
            )
            self._body.update(Text(what, style=palette["dim"]))
            return
        full, short, role = self.marks_for(palette).get(self.kind, ("", "", "dim"))
        label = short if self._compact else full
        self._gutter.update(
            Text(label, style=f"bold {palette[role]}", no_wrap=True,
                 overflow="ellipsis")
        )
        body_style = palette["error"] if self.kind == "error" else palette["foreground"]
        # A leading blank line would push the first words of the answer below
        # its own name — the exact fault the gutter exists to remove — and
        # models open with one often enough that it cannot be left to chance.
        self._body.update(Text(self.entry_text.lstrip("\r\n"), style=body_style))


class Masthead(Static):
    """The title plate, which repaints itself when its own width changes.

    The bench mode's plate is a self-sizing ``Panel`` and needs none of this.
    The DOS one is drawn to a measured width — the title is set *into* the top
    rule, so the two runs of rule either side of it have to add up — and a
    plate that keeps last size's arithmetic after a resize is a box with one
    corner past the edge of the window.

    Repainting from the *app's* resize handler is a frame too early: the
    handler runs before the layout that gives this widget its new width, and
    even a deferred callback can land before the arrangement settles. A widget
    is handed its ``Resize`` after it has been arranged, so this is the one
    moment at which the measurement is certainly the right one.
    """

    def on_resize(self, event: events.Resize) -> None:
        painter = getattr(self.app, "paint_masthead", None)
        if painter is None:
            return
        try:
            painter()
        except Exception:
            # A resize during teardown, or before the console has a palette.
            # The plate is chrome; it must not take the window down.
            pass


class BenchPane(Vertical):
    """The chat surface. This is the pane that has to work."""

    #: Entry kinds the transcript can hold. Kept as (kind, text) rather than
    #: as finished renderables so the whole transcript can be re-rendered
    #: under a new skin — see :meth:`restyle`. Storing styled Rich objects
    #: baked the active skin's hex values into every line, so switching skin
    #: left the existing conversation in the old palette: the reply stayed
    #: dark grey and the name stayed orange however light the new skin was.
    #: ``fold`` is the exception: a block of workings keeps its own contents,
    #: so it is recorded with empty text and re-renders itself.
    KINDS = ("user", "reply", "error", "note", "head", "kv", "fold")

    #: The kinds drawn with a name in the gutter. Everything else is console
    #: prose — a heading, a note — and starts at the left margin, because a
    #: gutter with nothing in it is just a wasted column.
    GUTTERED = ("user", "reply", "error", "kv")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._entries: list[tuple[str, str, Any]] = []
        self._reply: Entry | None = None
        #: Everything the model has said this turn, however many blocks it
        #: took. Reset when the turn ends, not when a block does.
        self._said = ""
        self._fold: Fold | None = None
        self._pen: Pen | None = None
        self._palette = None
        self._kit_name = ""
        self._compact = False

    # ── The transcript ───────────────────────────────────────────────────

    def _log(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    def _palette_now(self):
        return self._palette or _app_palette(self)

    def _append(self, widget) -> None:
        """Mount at the end. Following is the transcript's own business.

        There is deliberately no scroll call here: the anchor on
        :class:`Transcript` re-scrolls after the layout pass that this mount
        triggers, which is the only point at which the new height is known.
        """
        log = self._log()
        log.mount(widget)
        log.follow()

    def _follow(self) -> None:
        self._log().follow()

    # ── Writing ──────────────────────────────────────────────────────────

    def write(self, kind: str, text: str) -> None:
        """Add one block of conversation to the transcript."""
        self._reply = None
        widget = self._build(kind, text)
        self._entries.append((kind, text, widget))
        self._append(widget)

    def _build(self, kind: str, text: str):
        if kind in self.GUTTERED:
            entry = Entry(kind, text, self._palette_now())
            entry.set_compact(getattr(self, "_compact", False))
            return entry
        return Static(
            self._plain_text(kind, text), classes=f"entry entry-{kind}"
        )

    def clear_transcript(self) -> None:
        self._entries.clear()
        self._reply = None
        self._said = ""
        self._fold = None
        self._pen = None
        log = self._log()
        for child in list(log.children):
            child.remove()

    def restyle(self, palette) -> None:
        """Re-render every entry under a new skin's colours.

        A skin change has to reach the conversation already on screen, not
        just the chrome around it. Entries keep their kind and their text, so
        this is a re-render rather than a reconstruction.

        A *mode* change arrives the same way and has to reach further: the
        speaker marks, the fold markers and the composer's own prompt are all
        part of the skin, not decoration on top of one.
        """
        self._palette = palette
        for kind, text, widget in self._entries:
            if isinstance(widget, (Entry, Fold)):
                widget.restyle(palette)
            else:
                widget.update(self._plain_text(kind, text))
        self._restyle_composer(palette)

    def _restyle_composer(self, palette) -> None:
        """Put the input strip in the mode's own language.

        Two things: the mark in the gutter — a stencilled ``▶`` on the panel,
        a prompt chevron on the phosphor — and whether the cursor blinks. A
        blinking block cursor is the single most recognisable thing about a
        DOS prompt, and it is a real preference: it is also the thing that
        makes a terminal unusable for some readers, which is why it has a
        switch of its own rather than riding along with the mode.
        """
        is_dos = bool(getattr(palette, "dos", False))
        optics = dos.optics_of(palette)
        caret = self._maybe_one("#composer-caret", Static)
        if caret is not None:
            caret.update(
                Text(
                    dos.COMPOSER_CARET if is_dos else "▶",
                    style=f"bold {palette['primary']}",
                    no_wrap=True,
                )
            )
        composer = self._maybe_one("#composer", Composer)
        if composer is not None:
            composer.cursor_blink = bool(optics.block_cursor) if is_dos else True

    def _maybe_one(self, selector: str, kind: type):
        """A child of this pane, or None before it is mounted."""
        try:
            return self.query_one(selector, kind)
        except Exception:
            return None

    def set_compact(self, compact: bool) -> None:
        """Narrow the name gutter, here and for everything written later."""
        if compact == getattr(self, "_compact", False):
            return
        self._compact = compact
        log = self._maybe_log()
        if log is not None:
            log.set_class(compact, "-compact")
        for _kind, _text, widget in self._entries:
            if isinstance(widget, Entry):
                widget.set_compact(compact)

    def _maybe_log(self):
        try:
            return self._log()
        except Exception:
            return None

    def set_kit(self, name: str) -> None:
        """Adopt an indicator set, here and in any fold already on screen."""
        self._kit_name = name
        if self._pen is not None:
            self._pen.set_kit(name)

    def _plain_text(self, kind: str, text: str) -> Text:
        """A console line that is not part of the conversation."""
        palette = self._palette_now()
        if kind == "head":
            return Text(text, style=f"bold {palette['accent']}")
        return Text(text, style=palette["dim"])

    # ── Streaming a reply ────────────────────────────────────────────────

    def begin_reply(self) -> None:
        """Open a reply block. Idempotent within one reply.

        Starting an answer closes the block of workings above it. Anything
        the model does *after* this — a tool it reaches for having read what
        it just said — belongs to a new block, mounted below the answer,
        which is where the reader will look for it.
        """
        if self._reply is not None:
            return
        self._seal_fold()
        widget = Entry("reply", "", self._palette_now())
        widget.set_compact(getattr(self, "_compact", False))
        self._entries.append(("reply", "", widget))
        self._reply = widget
        self._append(widget)

    def append_reply(self, text: str) -> None:
        if self._reply is None:
            return
        self._said += text
        # Keep each entry's stored text in step with its widget so a later
        # restyle re-renders the whole reply, not the empty string it started
        # as. The entry holds its own text rather than sharing the turn's,
        # because a turn that goes answer, tool, answer has two of them and
        # the second must not be handed the first one's words.
        for index, (kind, grown, widget) in enumerate(self._entries):
            if widget is self._reply:
                grown += text
                self._entries[index] = (kind, grown, widget)
                self._reply.set_text(grown)
                break
        self._follow()

    @property
    def reply_text(self) -> str:
        """Everything the model has said this turn, across every block of it.

        A turn that answers, runs a tool and answers again is streamed into
        two separate entries, and the caller reading this wants the answer,
        not its last paragraph.
        """
        return self._said

    # ── Going back ───────────────────────────────────────────────────────

    def last_prompt(self) -> str | None:
        """The newest thing the user typed, as it appears in the transcript."""
        for kind, text, _widget in reversed(self._entries):
            if kind == "user":
                return text
        return None

    def drop_last_exchange(self) -> str | None:
        """Remove the newest user message and everything after it.

        Returns the prompt that was removed, so the caller can put it back in
        the composer or send it again. Everything after it goes too — the
        reply, the workings, any error — because those are answers to the
        question being taken away, and leaving them behind would make the
        transcript claim the agent said them unprompted.
        """
        cut = None
        for index in range(len(self._entries) - 1, -1, -1):
            if self._entries[index][0] == "user":
                cut = index
                break
        if cut is None:
            return None
        prompt = self._entries[cut][1]
        for _kind, _text, widget in self._entries[cut:]:
            self._remove_entry(widget)
        self._entries = self._entries[:cut]
        # Every block of workings the removed turn opened went with it — they
        # are entries too, so the loop above already took them. What is left
        # is the pane's own handle on whichever one was still open.
        self._fold = None
        self._pen = None
        self._reply = None
        self._said = ""
        self._follow()
        return prompt

    @staticmethod
    def _remove_entry(widget) -> None:
        """Unmount one entry, and the row it lives in if it shares one.

        A fold is mounted beside its pen inside a row of their own; removing
        the fold alone would leave the row and a parked pen behind as a blank
        line with a flat trace on it.
        """
        target = widget
        if isinstance(widget, Fold):
            target = widget.parent or widget
        try:
            target.remove()
        except Exception:
            # A widget already gone (a fold dropped as empty when its block
            # was sealed) is the expected case, not a fault.
            pass

    # ── The workings fold ────────────────────────────────────────────────

    def fold(self) -> Fold:
        """The block of workings currently open, opening one on first use.

        One per *block*, not one per turn. A turn is not
        workings-then-answer; it is however many rounds of the two the model
        needs, and each round's workings belong under the answer that
        prompted them. Keeping a single fold for the turn put every tool call
        a model made — including ones it decided on after reading its own
        first answer — into one drawer above that answer, which reads as the
        agent having done all of it up front and says nothing about what
        followed from what. :meth:`_seal_fold` closes the open block when an
        answer starts, so the next tool call opens a new one below it.

        The pen sits beside the heading rather than inside the fold, so the
        indicator is visible while the fold is shut — which is the whole
        point of it.
        """
        if self._fold is None:
            self._seal_reply()
            row = Horizontal(classes="entry fold-row")
            self._fold = Fold(on_grow=self._follow, classes="fold")
            self._pen = Pen(classes="pen")
            if self._kit_name:
                self._pen.set_kit(self._kit_name)
            # Into the entry list like everything else in the transcript, so
            # it takes its turn in the order, a rewind removes it, and a skin
            # change repaints it. Held outside the list, only the *open* fold
            # was ever reached by either.
            self._entries.append(("fold", "", self._fold))
            self._append(row)
            row.mount(self._fold)
            row.mount(self._pen)
            self._fold.restyle(self._palette_now())
        return self._fold

    @property
    def in_workings(self) -> bool:
        """Whether a block of workings is open right now.

        Asked by the console when a tool finishes, to know whether the turn
        is back in its workings or back in an answer. Read off the pane
        rather than tracked alongside it: the two would drift, and the pane
        is the one that decides.
        """
        return self._fold is not None

    def _seal_reply(self) -> None:
        """Close the streaming answer. What comes next goes below it.

        The counterpart to :meth:`_seal_fold`. Without it a turn that
        answered, ran a tool and answered again would append the second half
        of what it said to the entry *above* the tool call, so the transcript
        would show the tool running after words it had in fact produced
        before them.
        """
        self._reply = None

    def _seal_fold(self) -> None:
        """Close the open block of workings. The next one starts fresh.

        A block that collected nothing is removed rather than left as an
        empty control the reader can open onto nothing. :meth:`start_thinking`
        opens one before anything is known to fill it, so a turn that starts
        thinking and then answers with neither a tool call nor a token of
        reasoning would otherwise leave an empty drawer above its answer.
        """
        fold, self._fold = self._fold, None
        pen, self._pen = self._pen, None
        if pen is not None:
            pen.stop()
        if fold is not None and fold.collected_nothing:
            self._forget(fold)

    def _forget(self, fold: Fold) -> None:
        """Unmount a fold and take it out of the transcript's record."""
        self._entries = [
            entry for entry in self._entries if entry[2] is not fold
        ]
        self._remove_entry(fold)

    def start_thinking(self, state: str = THINKING) -> None:
        self.fold()
        if self._pen is not None:
            self._pen.start(state)

    def stop_thinking(self) -> None:
        if self._pen is not None:
            self._pen.stop()

    def end_turn(self) -> None:
        """Close the streaming reply and whatever block of workings is open.

        Called once the turn is over, so :attr:`reply_text` has to be read
        before this and not after — it is the turn's answer, and the turn is
        what is ending.
        """
        self._seal_reply()
        self._said = ""
        self.stop_thinking()
        self._seal_fold()

    # ── Mouse ────────────────────────────────────────────────────────────

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        """Right-clicking anywhere on the bench pastes into the composer.

        The transcript is most of the pane, and it is where a reader's
        pointer already is — requiring them to hit the input strip first
        would make the gesture useless where it is most wanted.
        """
        if event.button != _RIGHT_BUTTON:
            return
        composer = self.query_one("#composer", Composer)
        text = _clipboard_text(self)
        event.prevent_default()
        event.stop()
        composer.focus()
        if text:
            composer.insert(text)

    # ── Fold expansion ───────────────────────────────────────────────────

    @on(Collapsible.Expanded)
    @on(Collapsible.Collapsed)
    def _fold_toggled(self, event) -> None:
        """Opening the workings must not push the newest line off the screen.

        A fold holds a whole turn's reasoning: expanding it can add several
        screens of content in one layout pass, all of it *above* the answer.
        Without this, a reader who opened the thinking to watch it arrive had
        to scroll down after every expansion — which is the one thing the
        fold was supposed to save them. Collapsing gets the same treatment
        for the same reason, in the other direction.
        """
        self._log().follow()

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Masthead("", id="masthead")
        log = Transcript(id="transcript")
        log.can_focus = True
        yield log
        with Vertical(id="composer-frame"):
            # An empty row, not a caption. The keys it used to name are on
            # the key line and in F1, and it repeated them above a prompt the
            # reader was already typing into. The row itself stays: without
            # it the input sits hard against the rule above it, which reads
            # as a rendering fault rather than as a composer.
            yield Static("", id="composer-gap")
            with Horizontal(id="composer-row"):
                yield Static("▶", id="composer-caret")
                editor = Composer(id="composer", soft_wrap=True)
                editor.show_line_numbers = False
                yield editor


def _app_palette(widget):
    """The console's palette, read off the app that owns this widget."""
    return widget.app.bench_palette


def _is_dos(palette) -> bool:
    """Whether this palette was painted by the DOS mode."""
    return bool(getattr(palette, "dos", False))


def _palette(widget, token: str) -> str:
    try:
        return widget.app.bench_palette[token]
    except Exception:
        return ""


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


class LogbookPane(Vertical):
    """Every past conversation, most recently active first."""

    def compose(self) -> ComposeResult:
        yield _head("LOGBOOK — past conversations")
        table = DataTable(id="logbook-table", cursor_type="row", zebra_stripes=False)
        yield table
        yield _note(
            "Select a row to load that conversation onto the bench and carry "
            "on in it.  Ctrl+R reloads this list."
        )

    def on_mount(self) -> None:
        self.reload()

    def reload(self) -> None:
        """Re-read the session store into the table.

        Called on mount and after a turn, because a conversation started on
        the bench appears in this list only once it has been written, and the
        pane is often already mounted by then.
        """
        table = self.query_one("#logbook-table", DataTable)
        selected = self.selected_session_id()
        table.clear(columns=True)
        table.add_columns(*LOGBOOK_COLUMNS)
        rows = _read_sessions()
        for row in rows:
            table.add_row(*row, key=row[-1])
        if not rows:
            table.add_row(
                "—", "—", "—", "no conversations recorded yet", "—"
            )
            return
        if selected is not None:
            try:
                table.move_cursor(row=[r[-1] for r in rows].index(selected))
            except ValueError:
                pass

    def selected_session_id(self) -> str | None:
        """The id under the cursor, or None when the table has no real rows."""
        table = self.query_one("#logbook-table", DataTable)
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return None
        sid = str(row[-1]).strip()
        return sid if sid and sid != "—" else None

    def mark_active(self, session_id: str) -> None:
        """Flag which row is the conversation currently on the bench."""
        table = self.query_one("#logbook-table", DataTable)
        for index in range(table.row_count):
            try:
                row = table.get_row_at(index)
            except Exception:
                continue
            subject = str(row[3])
            plain = subject.removeprefix("▶ ").strip()
            table.update_cell_at(
                (index, 3),
                ("▶ " if str(row[-1]).strip() == session_id else "") + plain,
            )


class InstrumentsPane(Vertical):
    """Which model is wired up, and where the requests go."""

    def compose(self) -> ComposeResult:
        yield _head("INSTRUMENTS — model and route")
        table = DataTable(id="instrument-table", cursor_type="row")
        yield table
        yield _note(
            "Change any of this with:  curie model   ·   curie fallback   ·   curie moa"
        )

    def on_mount(self) -> None:
        table = self.query_one("#instrument-table", DataTable)
        table.add_columns("SETTING", "VALUE")
        for name, value in _read_runtime_settings():
            table.add_row(name, value)


class SupplyPane(Vertical):
    """Toolsets, skills and plugins — what the bench has to work with."""

    def compose(self) -> ComposeResult:
        yield _head("SUPPLY — toolsets, skills, plugins")
        table = DataTable(id="supply-table", cursor_type="row")
        yield table
        yield _note("Manage with:  curie tools   ·   curie skills   ·   curie plugins")

    def on_mount(self) -> None:
        table = self.query_one("#supply-table", DataTable)
        table.add_columns("KIND", "NAME", "STATE")
        rows = list(_read_supply())
        for row in rows:
            table.add_row(*row)
        if not rows:
            table.add_row("—", "nothing enumerable from here", "—")


class PanelPane(VerticalScroll):
    """Appearance and voice: display mode, skin, indicator set, microphone.

    Everything on this pane is the same kind of decision — how the console
    looks and how it talks — so they belong on one page rather than scattered
    behind four keys. It scrolls, because on a short window four tables and
    two sets of switches will not fit and the alternative is hiding one of
    them.

    The display block is first because it is the widest decision on the page:
    the mode decides what the console *is*, and the skin below it decides what
    colour the bench mode paints with. A reader who turns the DOS mode on and
    then wonders why the skin table stopped doing anything has been misled by
    the ordering, so the ordering says which contains which — and the note
    under the skin table says it in words while the mode is on.
    """

    #: Rows in the switch block, in order: (id, label, what it does).
    SWITCH_ROWS = (
        ("voice-listen", "MICROPHONE", "dictate into the composer"),
        ("voice-speak", "SPEAKER", "read replies aloud"),
    )

    #: The display block's switches. The two under the mode are its own
    #: settings and read as sub-settings of it, which is what the wording of
    #: their descriptions has to carry — Textual has no indentation here.
    DISPLAY_ROWS = (
        ("dos-mode", "DOS MODE", "draw this console as a phosphor terminal"),
        ("dos-scanlines", "SCANLINES", "· a dark line between every raster line"),
        ("dos-cursor", "BLOCK CURSOR", "· the composer's cursor blinks"),
    )

    def compose(self) -> ComposeResult:
        yield _head("DISPLAY — the console's skin")
        with Vertical(id="display-switches"):
            for switch_id, label, blurb in self.DISPLAY_ROWS:
                yield ToggleSwitch(switch_id, label, blurb, id=f"switch-{switch_id}")
        yield DataTable(id="phosphor-table", cursor_type="row")
        with Horizontal(id="display-controls"):
            yield PanelButton("◄ DIMMER", "dos-glow-down", id="glow-down")
            yield PanelButton("BRIGHTER ►", "dos-glow-up", id="glow-up")
            yield Static("", id="display-readout", classes="note")
        yield PhosphorPreview(id="phosphor-preview")
        yield Static("", id="display-note", classes="note")

        yield _head("PANEL — appearance")
        yield DataTable(id="panel-table", cursor_type="row")
        yield _note(
            "Select a skin to apply it here and everywhere else.  "
            "Or:  curie skin <name>"
        )

        yield _head("INDICATORS — the panel's moving figures")
        yield DataTable(id="indicator-table", cursor_type="row")
        yield Static("", id="indicator-preview-title", classes="note")
        yield KitPreview(id="indicator-preview")
        yield _note(
            "Select a set to change every animated indicator in the console.  "
            "Each one draws all of the states above; the preview is live."
        )

        yield _head("VOICE — dictation and speech")
        with Vertical(id="voice-switches"):
            for switch_id, label, blurb in self.SWITCH_ROWS:
                yield ToggleSwitch(switch_id, label, blurb, id=f"switch-{switch_id}")
        yield Static("", id="voice-state", classes="note")
        yield DataTable(id="voice-table", cursor_type="row")
        with Horizontal(id="voice-actions"):
            yield PanelButton("REFRESH VOICES", "voice-refresh", id="voice-refresh")
            yield Static("", id="voice-count", classes="note")
        yield Static("", id="voice-folder", classes="note")
        yield _note(
            "Select a voice to use it for spoken replies — that also makes "
            "Piper the TTS provider, since the folder is Piper's.  "
            "Drop a .onnx model (and its .onnx.json) in the folder and press "
            "REFRESH to pick it up without restarting."
        )

    def on_mount(self) -> None:
        tubes = self.query_one("#phosphor-table", DataTable)
        tubes.add_columns("PHOSPHOR", "TUBE", "WHAT IT IS LIKE")

        skins = self.query_one("#panel-table", DataTable)
        skins.add_columns("SKIN", "SOURCE", "DESCRIPTION")
        for row in _read_skins():
            skins.add_row(*row)

        kits = self.query_one("#indicator-table", DataTable)
        kits.add_columns("SET", "TITLE", "SAMPLE", "WHAT IT DRAWS")
        self.reload_kits()

        self.reload_display()
        self.reload_voices()
        self.refresh_switches()

    # ── The display mode ─────────────────────────────────────────────────

    def reload_display(self, settings=None) -> None:
        """Redraw the whole display block from the settings in force.

        One method rather than one per control, because every one of them
        changes what the others say: turning the mode off greys the tube
        table's meaning, changing the tube redraws the brightness preview, and
        the brightness readout names a step of a scale the preview is
        showing. Redrawing the block is cheaper than keeping five widgets in
        agreement with each other.
        """
        settings = settings if settings is not None else read_settings()
        dim = _palette(self, "dim")

        table = self.query_one("#phosphor-table", DataTable)
        table.clear()
        for name, title, blurb in dos.phosphor_catalogue():
            marker = "▶ " if name == settings.dos_phosphor else "  "
            table.add_row(marker + name, title, blurb)

        self.query_one("#display-readout", Static).update(
            Text(
                f"  GLOW {dos.glow_label(settings.dos_glow)}  "
                f"{dos.glow_bar(settings.dos_glow)}  "
                f"{settings.dos_glow}/{dos.GLOW_MAX}",
                style=dim,
            )
        )

        preview = self.query_one("#phosphor-preview", PhosphorPreview)
        preview.set_tube(
            settings.dos_phosphor, settings.dos_glow, settings.dos_scanlines
        )

        self.query_one("#display-note", Static).update(
            Text(
                "  The DOS mode paints this console only — the skin below it "
                "still dresses the CLI and the TUI, and takes this console "
                "back when the mode is off.\n"
                "  Every setting here is written to config.yaml as ui.skin_mode "
                "and ui.dos.*, so the console opens the way you left it.",
                style=dim,
            )
        )

    def refresh_display_switches(self, settings=None) -> None:
        """Put the three display switches where the stored settings say."""
        settings = settings if settings is not None else read_settings()
        for switch_id, state in (
            ("dos-mode", settings.dos_mode),
            ("dos-scanlines", settings.dos_scanlines),
            ("dos-cursor", settings.dos_block_cursor),
        ):
            try:
                self.query_one(f"#switch-{switch_id}", ToggleSwitch).set_on(state)
            except Exception:
                # Queried before the pane has finished mounting.
                return

    # ── Indicator sets ───────────────────────────────────────────────────

    def reload_kits(self, active: str | None = None) -> None:
        """Redraw the set table, flagging whichever set is in force."""
        table = self.query_one("#indicator-table", DataTable)
        active = active or read_settings().indicators
        table.clear()
        for name, title, blurb in kit_catalogue():
            marker = "▶ " if name == active else "  "
            table.add_row(marker + name, title, get_kit(name).sample(14), blurb)
        preview = self.query_one("#indicator-preview", KitPreview)
        preview.set_kit(active)
        self.query_one("#indicator-preview-title", Static).update(
            Text(
                f"  live preview — {get_kit(active).title.lower()}",
                style=_palette(self, "dim"),
            )
        )

    # ── Voice ────────────────────────────────────────────────────────────

    def reload_voices(self, selected: str | None = None) -> None:
        """Re-read the Piper folder. This is what REFRESH runs.

        Deliberately a fresh directory listing every time rather than a
        cache: the button exists because the folder changes while the console
        is open — a download finishing, a model copied in — and a cached
        answer would make the button do nothing visible.
        """
        table = self.query_one("#voice-table", DataTable)
        settings = read_settings()
        selected = selected if selected is not None else settings.piper_voice
        table.clear(columns=True)
        table.add_columns("VOICE", "SIZE", "STATE")
        voices = list_piper_voices(settings)
        for voice in voices:
            marker = "▶ " if voice.name == selected else "  "
            table.add_row(marker + voice.name, f"{voice.megabytes:.1f} MB", voice.state)
        if not voices:
            table.add_row("—", "—", "no voice models in the folder yet")
        dim = _palette(self, "dim")
        self.query_one("#voice-count", Static).update(
            Text(f"  {len(voices)} voice(s) found", style=dim)
        )
        # The folder on its own row: a path is long, and sharing a row with
        # the button left it one column short of fitting, wrapping onto a
        # second line the row was not tall enough to show. A path the reader
        # cannot see is the one thing this line exists to tell them.
        self.query_one("#voice-folder", Static).update(
            Text(f"  {piper_voices_dir(settings)}", style=dim)
        )

    def refresh_switches(self, listening: bool = False, speaking: bool = False,
                         note: str = "") -> None:
        """Redraw the two switches, and the line that explains them."""
        for switch_id, state in (("voice-listen", listening), ("voice-speak", speaking)):
            try:
                self.query_one(f"#switch-{switch_id}", ToggleSwitch).set_on(state)
            except Exception:
                # The pane can be queried before it has finished mounting.
                return
        state_line = self.query_one("#voice-state", Static)
        settings = read_settings()
        summary = note or (
            f"  provider {settings.tts_provider or 'edge'}"
            + (f"  ·  voice {settings.piper_voice}" if settings.piper_voice else "")
        )
        state_line.update(Text(summary, style=_palette(self, "dim")))


class ToggleSwitch(Static):
    """A labelled two-state switch, drawn the way the rail's switches are.

    Not Textual's ``Switch``: that widget is a rounded pill from a different
    design language, and next to a stencilled rail it reads as something
    pasted in from another program. This is a throw switch — a filled or
    empty position, a label, and a line saying what it does.
    """

    def __init__(self, key: str, label: str, blurb: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.switch_key = key
        self.switch_label = label
        self.switch_blurb = blurb
        self._on = False
        self.add_class("toggle")

    def set_on(self, value: bool) -> None:
        if value != self._on:
            self._on = bool(value)
            self.set_class(self._on, "-on")
            self.refresh()

    @property
    def is_on(self) -> bool:
        return self._on

    def render(self) -> Text:
        palette = _app_palette(self)
        # A throw switch on the panel; a bracketed check box on the phosphor.
        # Both say the same thing, and each is what its own interface used —
        # ▣ is not a glyph a text-mode program had, and [X] on the enamel
        # panel would read as a transcript of a screenshot.
        thrown, empty = ("[X] ", "[ ] ") if _is_dos(palette) else (" ▣ ", " ▢ ")
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append(
            thrown if self._on else empty,
            style=f"bold {palette['success' if self._on else 'dim']}",
        )
        # Fourteen, not twelve: BLOCK CURSOR is exactly twelve, which left the
        # state hard against the label with no gap to read it by.
        out.append(f"{self.switch_label:<14}", style=f"bold {palette['foreground']}")
        out.append("ON " if self._on else "OFF", style=palette[
            "success" if self._on else "dim"
        ])
        out.append(f"   {self.switch_blurb}", style=palette["dim"])
        return out

    def on_click(self) -> None:
        self.app.run_keyline_action(self.switch_key)


class PanelButton(Static):
    """A clickable action, drawn as a stencilled key rather than a button."""

    def __init__(self, label: str, action: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.button_label = label
        self.button_action = action
        self.add_class("panel-button")

    def render(self) -> Text:
        palette = _app_palette(self)
        # On the phosphor a button *is* an inverse-video run, so the label
        # takes the ink that goes on a band rather than the body colour —
        # which on that ground would be a hole rather than a key.
        ink = palette["ink"] if _is_dos(palette) else palette["foreground"]
        out = Text(no_wrap=True)
        out.append(f" {self.button_label} ", style=f"bold {ink}")
        return out

    def on_click(self) -> None:
        self.app.run_keyline_action(self.button_action)


class PhosphorPreview(Widget):
    """The five brightness steps of the chosen tube, on one page.

    A live sample rather than a swatch: each row is drawn with the palette
    that step actually resolves to, glass and all, so the reader is choosing
    from the thing being chosen. It is the same argument the indicator-set
    preview makes one section down, applied to the setting where it matters
    most — the trade a brightness control makes is invisible in a number.
    """

    #: One row per step of the brightness control, so the height is the
    #: length of the scale rather than a number that has to be kept in step
    #: with it by hand.
    DEFAULT_CSS = f"""
    PhosphorPreview {{
        height: {dos.GLOW_MAX - dos.GLOW_MIN + 1};
        margin-bottom: 1;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tube = dos.DEFAULT_PHOSPHOR
        self._glow = dos.DEFAULT_GLOW
        self._scanlines = True

    def set_tube(self, phosphor: str, glow: int, scanlines: bool = True) -> None:
        self._tube = dos.get_phosphor(phosphor).name
        self._glow = dos.clamp_glow(glow)
        self._scanlines = bool(scanlines)
        self.refresh()

    def render(self) -> Text:
        return dos.preview(
            self._tube,
            max(24, self.size.width or 48),
            chosen=self._glow,
            scanlines=self._scanlines,
        )


class DiagnosticsPane(Vertical):
    """Health checks, and the paths this install is actually using."""

    def compose(self) -> ComposeResult:
        yield _head("DIAGNOSTICS — install health")
        yield RichLog(id="diagnostics-log", wrap=True, markup=True, highlight=False)
        yield _note("Full report:  curie doctor   ·   Live view:  curie doctor --live")

    def on_mount(self) -> None:
        log = self.query_one("#diagnostics-log", RichLog)
        for line in _read_diagnostics():
            log.write(line)


# ── Data readers ─────────────────────────────────────────────────────────
# Each one is best-effort and returns a plain answer on failure. A pane that
# raises takes the whole console down; a pane that says "could not read this"
# is honest and keeps the rest usable.


#: Columns the logbook table shows, in order. ``ID`` is last and is what the
#: selection handler reads back, so it must stay last.
LOGBOOK_COLUMNS = ("LAST ACTIVE", "TURNS", "MODEL", "SUBJECT", "ID")


def _read_sessions(limit: int = 300) -> list[tuple[str, str, str, str, str]]:
    """Every past conversation, most recently active first.

    Not just the named ones. A session gets a title only when something names
    it, which for a chat someone just opened and typed into is never — so
    filtering on a title (which the CLI's own picker does, to keep its list
    short) hid the majority of real conversations from this pane. Here the
    list is the record of work, so an untitled session is listed under the
    opening line of the conversation instead of being dropped.
    """
    db = _session_db()
    if db is None:
        return []
    try:
        rows = db.list_sessions_rich(
            limit=limit,
            order_by_last_active=True,
            compact_rows=True,
        )
    except Exception:
        return []

    out: list[tuple[str, str, str, str, str]] = []
    # The id is the table's row key, and a duplicate key raises rather than
    # being ignored — so one repeated id would take the whole pane down.
    # Compression chains are projected forward to their live tip, which is
    # exactly the shape that can surface the same id twice.
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        when = _when(row.get("last_active") or row.get("started_at"))
        turns = row.get("message_count")
        subject = (
            str(row.get("title") or "").strip()
            or str(row.get("preview") or "").strip()
            or "untitled"
        )
        out.append(
            (
                when,
                f"{turns:,}" if isinstance(turns, int) else "—",
                str(row.get("model") or "—"),
                " ".join(subject.split())[:64],
                sid,
            )
        )
    return out


def _when(value: Any) -> str:
    """Render a session timestamp as something a person can read.

    The stored value is whichever of the heartbeat, the newest message and the
    start time is freshest, and those are not all the same shape: rows carry
    epoch seconds in some places and an ISO string in others. Printed raw, an
    epoch float reaches the column as ``1788407323.60770``, which tells the
    reader nothing about when anything happened.
    """
    if value in (None, ""):
        return "—"
    try:
        stamp = datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OSError, OverflowError):
        # Not a number, or a number no calendar can hold — an out-of-range
        # epoch raises OSError/OverflowError rather than ValueError, and the
        # logbook is a readout: it shows what it found, it does not crash.
        text = str(value).strip().replace("T", " ")
        return text[:16] or "—"
    return stamp.strftime("%Y-%m-%d %H:%M")


def _session_db():
    """The shared session store, or None when it cannot be opened.

    The logbook is a read of state the rest of Curie owns; if the database is
    not there (first run, unwritable home) the pane says so rather than
    inventing rows.
    """
    try:
        from curie_state import SessionDB

        return SessionDB()
    except Exception:
        return None


def _read_runtime_settings() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    try:
        from curie_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
    except Exception:
        return [("config", "could not be read")]

    model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    approvals = cfg.get("approvals") if isinstance(cfg.get("approvals"), dict) else {}

    def add(label: str, value: Any, fallback: str = "not set") -> None:
        text = str(value).strip() if value not in (None, "") else ""
        rows.append((label, text or fallback))

    add("model", model.get("default") or model.get("model"))
    add("provider", model.get("provider"), "auto")
    add("base_url", model.get("base_url"), "provider default")
    add("context length", model.get("context_length"), "detected at runtime")
    add("reasoning effort", agent.get("reasoning_effort"), "provider default")
    add("max turns", agent.get("max_turns"))
    add("approvals mode", approvals.get("mode"), "smart")
    try:
        from curie_constants import get_curie_home

        rows.append(("curie home", str(get_curie_home())))
    except Exception:
        pass
    return rows


def _read_supply() -> Iterable[tuple[str, str, str]]:
    try:
        from curie_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
    except Exception:
        return []

    out: list[tuple[str, str, str]] = []

    toolsets = cfg.get("toolsets")
    if isinstance(toolsets, dict):
        for name, state in sorted(toolsets.items()):
            if isinstance(state, bool):
                out.append(("toolset", str(name), "enabled" if state else "disabled"))

    skills = cfg.get("skills")
    if isinstance(skills, dict):
        disabled = skills.get("disabled")
        if isinstance(disabled, list):
            for name in sorted(str(s) for s in disabled):
                out.append(("skill", name, "disabled"))

    servers = cfg.get("mcp_servers")
    if isinstance(servers, dict):
        for name in sorted(servers):
            out.append(("mcp server", str(name), "configured"))

    return out


def _read_skins() -> list[tuple[str, str, str]]:
    try:
        from curie_cli.skin_engine import get_active_skin, list_skins
    except Exception:
        return [("—", "—", "skin engine unavailable")]
    try:
        active = get_active_skin().name
    except Exception:
        active = ""
    rows: list[tuple[str, str, str]] = []
    try:
        entries = list_skins()
    except Exception:
        return [("—", "—", "could not list skins")]
    for entry in entries or []:
        if isinstance(entry, dict):
            name = str(entry.get("name", ""))
            source = str(entry.get("source", "") or "builtin")
            desc = str(entry.get("description", ""))
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            name, source, desc = (list(entry) + ["", ""])[:3]
            name, source, desc = str(name), str(source or "builtin"), str(desc)
        else:
            name, source, desc = str(entry), "builtin", ""
        marker = "▶ " if name == active else "  "
        rows.append((marker + name, source, desc))
    return rows


def _read_diagnostics() -> list[Text]:
    lines: list[Text] = []

    def row(label: str, value: str, ok: bool | None = None) -> None:
        text = Text()
        if ok is None:
            text.append("  ·  ", style="dim")
        else:
            text.append("  ✓  " if ok else "  ✗  ", style="green" if ok else "red")
        text.append(f"{label:<22}", style="bold")
        text.append(value)
        lines.append(text)

    try:
        from curie_constants import get_curie_home

        home = get_curie_home()
        row("curie home", str(home), home.exists())
        cfg = home / "config.yaml"
        row("config.yaml", str(cfg), cfg.exists())
        soul = home / "SOUL.md"
        if soul.exists():
            size = soul.stat().st_size
            row("SOUL.md", f"{size:,} bytes — the agent's voice", size > 0)
        else:
            row("SOUL.md", "not present — the built-in default applies", None)
    except Exception as exc:  # noqa: BLE001
        row("curie home", f"could not resolve: {exc}", False)

    try:
        import sys

        row("python", sys.version.split()[0], True)
        row("interpreter", sys.executable, True)
    except Exception:
        pass

    for var in ("CURIE_HOME", "CURIE_INFERENCE_MODEL", "CURIE_UI_POLARITY"):
        value = os.environ.get(var, "")
        row(var, value or "not set", None)

    legacy = [k for k in os.environ if k.startswith("HERMES_")]
    if legacy:
        row(
            "legacy env",
            f"{len(legacy)} HERMES_* variable(s) adopted as CURIE_* — "
            "rename them when convenient",
            None,
        )

    lines.append(Text(""))
    lines.append(Text("  Run `curie doctor` for the full report.", style="dim"))
    return lines


__all__ = [
    "BenchPane",
    "Composer",
    "DiagnosticsPane",
    "Entry",
    "Fold",
    "InstrumentsPane",
    "LogbookPane",
    "Masthead",
    "PanelButton",
    "PanelPane",
    "Pen",
    "PhosphorPreview",
    "SupplyPane",
    "ToggleSwitch",
    "Transcript",
]
