"""The console's stylesheet.

Textual CSS, with the palette injected as ``$bench-*`` variables by the app
before parsing (see :mod:`curie_cli.bench_ui.theme`). Nothing here hard-codes
a colour: every value is a variable, so the whole console re-themes when the
skin changes.

The layout is a docked frame — title bar at the top, function-key line at the
bottom, a switch rail on the left, content in the middle — because that is
how a panel is built and because docking is what survives a resize. Every
dimension is either a fraction or a minimum, so the console reflows from a
200-column window down to about 46 columns, below which the rail collapses to
icons and then hides entirely.
"""

from __future__ import annotations

BENCH_CSS = """
Screen {
    background: $bench-background;
    color: $bench-foreground;
    layers: base overlay;
}

/* ── Title bar ─────────────────────────────────────────────────────── */

#titlebar {
    dock: top;
    height: 2;
    background: $bench-panel;
    border-bottom: heavy $bench-border;
    padding: 0 1;
    layout: horizontal;
}

#titlebar-mark {
    width: auto;
    min-width: 9;
    color: $bench-accent;
    text-style: bold;
    content-align: left middle;
    height: 100%;
}

#titlebar-subject {
    width: 1fr;
    color: $bench-dim;
    content-align: left middle;
    height: 100%;
    padding: 0 2;
}

#titlebar-lamps {
    width: auto;
    height: 100%;
    content-align: right middle;
}

#titlebar-clock {
    width: auto;
    min-width: 8;
    color: $bench-secondary;
    content-align: right middle;
    height: 100%;
    padding: 0 0 0 2;
}

/* ── Function-key line ─────────────────────────────────────────────── */

#keyline {
    dock: bottom;
    height: 1;
    background: $bench-panel;
    color: $bench-foreground;
    layout: horizontal;
}

.keycap {
    width: auto;
    padding: 0 1;
    color: $bench-foreground;
}

.keycap:hover {
    background: $bench-selection;
    color: $bench-accent;
    text-style: bold;
}

/* The second tier of keycaps, dropped on a narrow window. The keys keep
   working; only the painted reminder goes. */
.keycap.hidden {
    display: none;
}

#keyline-spacer {
    width: 1fr;
}

#keyline-note {
    width: auto;
    padding: 0 1;
    color: $bench-dim;
}

/* F10 takes the key line's *lettering* away with the title plate, not the
   bar itself. The bar is a painted edge along the bottom of the window in
   the skin's panel colour — removing it left the composer sitting directly
   on the terminal's own background with nothing closing the frame, which
   read as the window having lost its bottom rather than as a cleaner one.
   So the row and its colour stay, and the caps inside it go. */
#keyline.-blank .keycap,
#keyline.-blank #keyline-note {
    visibility: hidden;
}

/* ── Switch rail ───────────────────────────────────────────────────── */

#body {
    width: 1fr;
    height: 1fr;
    layout: horizontal;
}

#rail {
    width: 18;
    background: $bench-panel;
    border-right: heavy $bench-border;
    padding: 1 0;
}

#rail.narrow {
    width: 5;
}

.switch {
    width: 100%;
    height: 2;
    padding: 0 1;
    color: $bench-foreground;
    border-left: outer $bench-panel;
    content-align: left middle;
}

.switch:hover {
    background: $bench-selection;
    border-left: outer $bench-primary;
}

.switch.-active {
    background: $bench-selection;
    color: $bench-accent;
    text-style: bold;
    border-left: outer $bench-primary;
}

/* ── Content ───────────────────────────────────────────────────────── */

#content {
    width: 1fr;
    height: 1fr;
    padding: 0;
}

.pane {
    width: 1fr;
    height: 1fr;
    padding: 1 1;
}

/* F10 asks for reading room, so it takes the pane's own top padding too.
   Left in, the freed rows were spent on the gap they were freed from: the
   title plate went and its blank row stayed. Only the top — the sides and
   the bottom still hold the content off the rules beside it. */
.pane.-tight {
    padding: 0 1 1 1;
}

.section-head {
    height: 1;
    color: $bench-accent;
    text-style: bold;
    margin-bottom: 1;
}

.field-label {
    color: $bench-secondary;
}

.readout {
    color: $bench-foreground;
}

.note {
    color: $bench-dim;
}

/* ── Bench (chat) pane ─────────────────────────────────────────────── */

#transcript {
    width: 1fr;
    height: 1fr;
    /* No border of its own. The rail and the instrument stack already frame
       this column with heavy rules, and a third box inside those read as
       clutter while costing two rows and two columns of the reading width —
       which is the one measurement in this console that should be generous. */
    border: none;
    /* One row of air above the first line and below the last, to match the
       two columns at each side.

       Two columns and one row is what *even* means on a character grid: a
       terminal cell is about twice as tall as it is wide, so equal counts
       would not read as equal margins — two rows top and bottom would look
       like a gap and cost two lines of a column that is already the
       shortest thing in the window. One row reads as the same inset the
       sides have, which is what it is.

       Without it the transcript had no vertical inset at all, and the fault
       only appeared once the conversation was long enough to fill the pane:
       the newest reply sat welded to the composer's rule and the oldest
       visible one to the title plate's, with clean margins either side of
       both. Padding rather than a margin on the entries, because a margin
       is part of the scrolled content — it would ride up out of sight the
       moment the reader scrolled, which is exactly when the gap is wanted. */
    padding: 1 1;
    background: $bench-background;
    /* A chat transcript scrolls one way. Text that needs sideways scrolling
       to read is text the reader cannot read, and the bar itself eats a row
       of an already-short pane. Entries are widgets, so they rewrap
       themselves at whatever width they are given. */
    overflow-x: hidden;
    overflow-y: auto;
    scrollbar-color: $bench-border;
    scrollbar-color-hover: $bench-primary;
    scrollbar-background: $bench-panel;
}

/* PANEL → READING → SCROLL BARS, off. Zero *size* rather than
   `overflow-y: hidden`: hidden would stop the pane scrolling at all, which
   is not what the switch says and would strand the reader at the bottom of
   a long conversation. The column the bar was using goes back to the text,
   and the wheel, the keys and the mouse still scroll it. */
#transcript.-no-scrollbar {
    scrollbar-size-vertical: 0;
}

/* The title plate. A sibling above the transcript rather than an entry
   inside it, so F10 can take it away and the transcript — the flexible
   sibling — takes the freed rows with nothing else to do. */
#masthead {
    width: 1fr;
    height: auto;
    padding: 0 1;
}

/* One block of the transcript: a user line, a reply, an error, a note. */
.entry {
    width: 1fr;
    height: auto;
}

/* A little air around each turn, set here rather than by padding blank
   lines into the text — a margin cannot be left behind by a rewrap. */
.entry-user {
    margin-top: 1;
}

.entry-reply {
    margin-top: 1;
}

/* ...but not above the first one once F10 has taken the plate away. That
   margin is air *between* turns; on the opening entry it is a blank row
   under the title plate, which is right while the plate is there and is one
   of two rows holding the conversation off the top of the window once it is
   not. Scoped to the hidden state so the ordinary layout is unchanged. */
.pane.-tight #transcript > .entry:first-of-type {
    margin-top: 0;
}

/* The name column. Fixed width, so a reply and the message it answers start
   at the same place and the whole conversation reads down one edge — which
   is the point of putting the name in a gutter rather than in front of the
   first line. Eight columns fits "▮ CURIE" with a space after it. */
.entry-gutter {
    width: 8;
    height: auto;
    text-style: bold;
}

/* Every line of the text, beside the name and never under it. */
.entry-body {
    width: 1fr;
    height: auto;
}

/* On a narrow window the name shrinks to its glyph and the reading column
   takes the six columns back. Set on the entry rather than by a second
   widget, so an entry already on screen re-lays out in place. */
.entry.-compact > .entry-gutter {
    width: 2;
}

/* Key/description pairs (the command index) use the same two columns with a
   wider gutter, because a key name is longer than a speaker's. */
.entry-pair > .entry-gutter {
    width: 16;
    padding: 0 0 0 2;
}

#transcript.-compact .entry-pair > .entry-gutter {
    width: 13;
    padding: 0;
}

/* The workings fold and its thinking indicator, side by side, so the pen is
   visible while the fold is shut — which is the whole point of it. */
.fold-row {
    width: 1fr;
    height: auto;
    margin-top: 1;
}

.fold-row > Collapsible {
    width: 1fr;
}

.pen {
    width: 16;
    height: 1;
    padding: 0 1;
    content-align: right middle;
}

/* ── Folded workings ──────────────────────────────────────────────────
   Reasoning and tool calls, shut by default. Styled to read as a drawer in
   the panel rather than as a widget from another program: no rounded
   chrome, a single rule above it, and the title in the label colour so a
   shut fold is legible as a summary line and not as a button. */

Collapsible {
    width: 1fr;
    height: auto;
    background: $bench-background;
    border-top: none;
    padding: 0;
    margin: 0;
}

Collapsible > CollapsibleTitle {
    width: 1fr;
    padding: 0 1;
    color: $bench-secondary;
    background: $bench-background;
    text-style: none;
}

Collapsible > CollapsibleTitle:hover {
    background: $bench-selection;
    color: $bench-accent;
}

Collapsible > CollapsibleTitle:focus {
    background: $bench-selection;
    color: $bench-accent;
    text-style: bold;
}

Collapsible > Contents {
    padding: 0 0 0 2;
    height: auto;
}

.fold-body {
    width: 1fr;
    height: auto;
    color: $bench-dim;
}

#composer-frame {
    dock: bottom;
    height: auto;
    max-height: 12;
    border-top: heavy $bench-border;
    padding: 0;
    background: $bench-background;
}

/* One row at rest, growing with what is typed into it.
   The minimum used to be three, which put two empty rows under a one-line
   message and, with the pane's own bottom padding under that, three blank
   rows between the caret and the key line. An input box is recognisable from
   its rule, its caret and the room above it; the extra rows were reading
   room the transcript could have had, and it now does — a shorter composer
   is docked at the bottom, so every row it gives up goes to the conversation. */
#composer-row {
    height: auto;
    min-height: 1;
    max-height: 8;
    layout: horizontal;
}

/* A stencilled caret in its own gutter, so the input area is visibly an
   input area even when empty. Without it the composer is three blank rows
   the eye slides straight past. */
#composer-caret {
    width: 3;
    height: 100%;
    color: $bench-primary;
    text-style: bold;
    content-align: center top;
    padding: 0 1;
}

#composer {
    width: 1fr;
    height: auto;
    min-height: 1;
    max-height: 8;
    border: none;
    background: $bench-background;
    color: $bench-foreground;
    padding: 0 1;
}

#composer:focus {
    border: none;
}

/* The blank row between the composer's rule and its input. */
#composer-gap {
    height: 1;
}

#turn-strip {
    dock: bottom;
    height: 4;
    border-top: solid $bench-rule;
    padding: 0 1;
}

/* ── Instrument row ────────────────────────────────────────────────── */

#instruments {
    width: 34;
    min-width: 26;
    background: $bench-panel;
    border-left: heavy $bench-border;
    padding: 1 1;
    /* The stack has a fixed-height chart and a gauge in it, so on a short
       terminal it is taller than the column it lives in. Scrolling is the
       honest answer: clipping would take the readouts off the bottom with
       nothing to say they were there. */
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

#instruments.hidden {
    display: none;
}

.instrument-title {
    height: 1;
    color: $bench-secondary;
    text-style: bold;
    margin-top: 1;
}

/* The state figure. A minimum rather than a fixed height so a short window
   gives it two rows (figure plus caption) instead of clipping the caption
   off, and 1fr so a tall one gives it the slack — it is the instrument
   worth growing, because it is the only one that is a picture. */
#activity {
    height: 1fr;
    min-height: 3;
    max-height: 7;
}

/* The gauge answers "how close to the limit" and the tape answers "how far
   into the turn": two different questions, two different scales, and stacked
   flush they read as one three-row instrument with a stray bar under it. The
   gauge's own bottom row is a numeric scale — 0 at the left, the ceiling at
   the right — and the tape's fill started on the very next row, so the eye
   took the fill for part of the scale. One row of air is what separates two
   instruments from one; every other pair in this stack has a stencilled
   title doing the same job, and these two deliberately have none. */
#gauge-context {
    margin-bottom: 1;
}

#chart-legend {
    height: 1;
    color: $bench-dim;
}

/* ── The settings pane ─────────────────────────────────────────────────
   Appearance, indicator sets and voice, on one scrolling page. The tables
   size to their contents rather than to 1fr: three tables each claiming an
   equal share of the pane left every one of them two rows tall. */

#pane-panel {
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

#pane-panel DataTable {
    height: auto;
    max-height: 14;
    margin-bottom: 1;
}

/* The display block: the mode switch, the tube table, the brightness
   control and its preview. Laid out here rather than in the mode's own
   stylesheet, because these widgets are on the pane in both modes — the DOS
   half only says what colour they are. */

#display-switches {
    height: auto;
    margin-bottom: 1;
}

#reading-switches {
    height: auto;
    margin-bottom: 1;
}

#display-controls {
    height: 1;
    layout: horizontal;
    margin-bottom: 1;
}

#display-readout {
    width: 1fr;
    height: 1;
    padding: 0 1;
}

#display-note {
    height: auto;
    margin-bottom: 1;
}

#reading-note {
    height: auto;
    margin-bottom: 1;
}

#indicator-preview {
    height: auto;
    padding: 0 1;
    margin-bottom: 1;
}

#indicator-preview-title {
    height: 1;
}

#voice-switches {
    height: auto;
    margin-bottom: 1;
}

.toggle {
    width: 1fr;
    height: 1;
    color: $bench-foreground;
    border-left: outer $bench-panel;
}

.toggle:hover {
    background: $bench-selection;
    border-left: outer $bench-primary;
}

.toggle.-on {
    border-left: outer $bench-success;
}

#voice-state {
    height: 1;
    margin-bottom: 1;
}

#voice-actions {
    height: 1;
    layout: horizontal;
    margin-bottom: 1;
}

.panel-button {
    width: auto;
    height: 1;
    background: $bench-panel;
    color: $bench-foreground;
    text-style: bold;
}

.panel-button:hover {
    background: $bench-selection;
    color: $bench-accent;
}

#voice-count {
    width: 1fr;
    height: 1;
    padding: 0 1;
}

#voice-folder {
    width: 1fr;
    height: 1;
    margin-bottom: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

/* ── The schedule pane ─────────────────────────────────────────────────
   Three surfaces sharing one pane: the task list, the editor, and a window
   onto one run. Only ever two of them are on screen at once — the list and
   the form swap, and the window docks under whichever is showing — so every
   height here is a fraction or an auto, and the one flexible child is
   whichever of the two is visible. That is what makes the pane correct at
   every combination of states rather than at the three that were tried. */

#pane-schedule {
    layout: vertical;
}

#schedule-list {
    width: 1fr;
    height: 1fr;
    layout: vertical;
}

#schedule-summary {
    height: 1;
    color: $bench-foreground;
    margin-bottom: 1;
}

#schedule-table {
    width: 1fr;
    height: 1fr;
    min-height: 4;
}

/* Docked so the task window takes its rows from the table above, never from
   the controls: a window big enough to work in must not be able to cover the
   button that closes it. */
#schedule-footer {
    dock: bottom;
    width: 1fr;
    height: auto;
    layout: vertical;
}

#schedule-actions {
    height: 1;
    layout: horizontal;
    margin-top: 1;
}

#schedule-scheduler-note {
    height: 1;
    margin-top: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

/* The editor. Scrolls, because eleven controls and a live preview do not fit
   a short window — and the alternative to scrolling is hiding one of them,
   which for a form means a field the reader cannot find. */

#schedule-form {
    width: 1fr;
    height: 1fr;
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

.field-row {
    height: 1;
    layout: horizontal;
    margin-bottom: 1;
}

.field-name {
    width: 10;
    height: 1;
    color: $bench-secondary;
    text-style: bold;
}

.field-name-block {
    height: 1;
    color: $bench-secondary;
    text-style: bold;
}

.field-hint {
    width: 1fr;
    height: 1;
    color: $bench-dim;
}

/* Textual's Input is a bordered box three rows tall out of the box, which in
   a form of eleven fields is thirty-three rows of border. Flattened to one
   row with a rule under it: the same affordance, a third of the height, and
   it reads as a field on a panel rather than as a widget from a web form. */
#schedule-form Input {
    height: 1;
    width: 1fr;
    border: none;
    padding: 0 1;
    background: $bench-panel;
    color: $bench-foreground;
}

#schedule-form Input:focus {
    border: none;
    background: $bench-selection;
    color: $bench-accent;
}

#schedule-every-value,
#schedule-time,
#schedule-date,
#schedule-repeat-times {
    width: 14;
}

#schedule-prompt {
    height: 6;
    min-height: 3;
    max-height: 12;
    width: 1fr;
    border: none;
    padding: 0 1;
    margin-bottom: 1;
    background: $bench-panel;
    color: $bench-foreground;
}

#schedule-prompt:focus {
    border: none;
    background: $bench-selection;
}

#schedule-modes {
    height: 1;
    layout: horizontal;
    margin-bottom: 1;
}

.mode-tab {
    width: auto;
    height: 1;
    margin-right: 1;
}

.mode-tab:hover {
    background: $bench-selection;
}

.mode-tab.-active {
    background: $bench-selection;
}

.cycler {
    width: auto;
    min-width: 16;
    height: 1;
    padding: 0 1;
}

.cycler:hover {
    background: $bench-selection;
}

#schedule-form-preview {
    height: auto;
    max-height: 2;
    margin-top: 1;
}

#schedule-form-problem {
    height: auto;
    max-height: 2;
    color: $bench-error;
}

#schedule-form-actions {
    height: 1;
    layout: horizontal;
    margin-top: 1;
}

/* ── The task window ───────────────────────────────────────────────────
   Docked under whichever surface is showing, so it takes rows from it
   rather than covering it. A fraction rather than a row count: on a tall
   terminal the window should be worth reading and on a short one the list
   above it still has to be usable, and only a fraction is both. */

#task-window {
    dock: bottom;
    width: 1fr;
    height: 45%;
    min-height: 8;
    layout: vertical;
    border-top: heavy $bench-border;
    background: $bench-background;
    padding: 0;
}

#preview-bar {
    height: 1;
    layout: horizontal;
    background: $bench-panel;
}

#preview-title {
    width: 1fr;
    height: 1;
    padding: 0 1;
}

#preview-log {
    width: 1fr;
    height: 1fr;
    padding: 1 1;
    overflow-x: hidden;
    overflow-y: auto;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

/* The air between two messages goes *above* the second, not below the
   first. Below, the newest message is followed by a blank row that is part
   of the scrollable content — so a window scrolled to its end shows the gap
   instead of the reply, which in a three-row window is the whole of it. */
.preview-entry {
    width: 1fr;
    height: auto;
    margin-top: 1;
}

.preview-entry:first-of-type {
    margin-top: 0;
}

.preview-empty {
    width: 1fr;
    height: auto;
    color: $bench-dim;
}

/* ``auto``, so an empty status line is no rows at all. Fixed at one, the
   window spent a row on nothing for the whole of its life and only used it
   when something went wrong. */
#preview-status {
    height: auto;
    max-height: 2;
    color: $bench-warning;
}

#preview-composer-row {
    height: auto;
    min-height: 1;
    max-height: 5;
    layout: horizontal;
    border-top: solid $bench-border;
}

#preview-caret {
    width: 3;
    height: 100%;
    color: $bench-primary;
    text-style: bold;
    content-align: center top;
    padding: 0 1;
}

#preview-composer {
    width: 1fr;
    height: auto;
    min-height: 1;
    max-height: 4;
    border: none;
    background: $bench-background;
    color: $bench-foreground;
    padding: 0 1;
}

#preview-composer:focus {
    border: none;
}

/* ── Tables and logs ───────────────────────────────────────────────── */

DataTable {
    height: 1fr;
    background: $bench-background;
    color: $bench-foreground;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

DataTable > .datatable--header {
    background: $bench-panel;
    color: $bench-accent;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: $bench-selection;
    color: $bench-accent;
}

DataTable > .datatable--hover {
    background: $bench-selection;
}

RichLog {
    background: $bench-background;
    color: $bench-foreground;
    border: round $bench-border;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-panel;
}

/* ── Buttons drawn as panel switches, not app buttons ─────────────── */

Button {
    background: $bench-panel;
    color: $bench-foreground;
    border: none;
    height: 3;
    min-width: 12;
    text-style: none;
}

Button:hover {
    background: $bench-selection;
    color: $bench-accent;
    text-style: bold;
}

Button.-primary {
    background: $bench-primary;
    color: $bench-background;
    text-style: bold;
}

Button.-primary:hover {
    background: $bench-accent;
}

/* ── Notices ──────────────────────────────────────────────────────── */

#notice {
    height: auto;
    max-height: 4;
    padding: 0 2;
    background: $bench-panel;
    color: $bench-warning;
    border-bottom: solid $bench-warning;
}

#notice.hidden {
    display: none;
}

Tooltip {
    background: $bench-panel;
    color: $bench-foreground;
    border: round $bench-border;
}
"""

__all__ = ["BENCH_CSS"]
