---
sidebar_position: 4
title: "The bench console (curie ui)"
description: "A full-screen, mouse-driven console for running turns, reading the logbook, and checking what your install is wired up to."
---

# The bench console

```bash
curie ui
```

A full-screen console driven by pointer as much as by keyboard. It runs turns,
lists every past conversation, shows what model and route this install is
actually using, and reports install health — with live instruments along the
right-hand side that say what it is doing while it does it.

It is themed by the active [skin](./features/skins.md), so it looks like the
rest of Curie and changes with it, and its animated indicators come in
[selectable sets](#indicator-sets) chosen on the same page as the skin.

It also has a second skin of its own: [DOS mode](#dos-mode) re-draws the whole
console as an amber phosphor terminal.

## What is where

The rail down the left side is a set of labelled switches. Click one, or press
its function key.

| Switch | Key | What it holds |
|--------|-----|---------------|
| **BENCH** | `F2` | The chat surface. Type below, press Enter. |
| **SCHEDULE** | `Ctrl+T` | [Automated tasks](#schedule) — what they do, when they fire, and a window onto the one that is running. |
| **LOGBOOK** | `F3` | Every past conversation, most recently active first. Select one to load it onto the bench and carry on in it. |
| **INSTRUMENTS** | `F4` | Model, provider, base URL, context length, reasoning effort, approvals mode, and where `CURIE_HOME` resolved. |
| **SUPPLY** | `F5` | Toolsets and their enable state, disabled skills, configured MCP servers. |
| **PANEL** | `F6` | Settings: the [display mode](#dos-mode), every available skin, the [indicator set](#indicator-sets) the panel draws with, and the [voice controls](#voice). |
| **DIAGNOSTICS** | `F9` | Whether `config.yaml` and `SOUL.md` exist and how big they are, the interpreter in use, and any legacy `HERMES_*` variables still set. |

## Keys

| Key | Does |
|-----|------|
| `Enter` | Send the composed request |
| `Shift+Enter` | Newline inside the composer |
| `Ctrl+J` | Newline — works in every terminal, including ones that cannot report `Shift+Enter` |
| `Ctrl+C` | Stop the running turn |
| `Ctrl+L` | Clear the transcript |
| `Ctrl+Q` | Close the console |
| `F1` | The command index |
| `Ctrl+T` | The SCHEDULE pane |
| `F7` | Show or hide the rail |
| `F8` | Show or hide the instrument stack |
| `F10` | Show or hide the chrome — the title plate, the key line's lettering and notices |
| `Ctrl+G` | Ask the last request again, without the previous answer |
| `Ctrl+B` | Take back one message, into the composer |
| `F12` | Start a new conversation |
| `Ctrl+R` | Re-read the logbook |
| right-click | Paste the clipboard into the composer |

Everything the keys do, the mouse does too: switches, function-key captions,
folds, table rows and scrollbars are all clickable.

The function keys belong to the console at all times, including while you are
typing in the composer — they are not editor keys that the composer happens to
forward.

SCHEDULE is on a control key rather than a function key because there was no
function key left: `F1` to `F10` are all spoken for, `F12` starts a new
conversation, and `F11` is the window manager's fullscreen toggle in
essentially every terminal, so an application never sees it. In DOS mode the
menu line prints `^T)` where the other panes print their number — what the
menu shows is always the key that throws it.

## The logbook

Every conversation Curie has recorded, not only the named ones. A session gets
a title when something names it, which for a chat you simply opened and typed
into is never, so those are listed under their opening line instead of being
left out.

Selecting a row loads that conversation onto the bench: the transcript is
redrawn and the next turn continues it, writing back to the same session. A
conversation that has been compressed resolves to its live continuation, so
you carry on where the work actually is rather than in a stale root.

Conversations started on the bench are recorded like any other, so they appear
here — and in `curie --resume` — once the first turn has been written.

## Schedule

`Ctrl+T` opens the automated tasks. A task is a prompt and a time: the prompt
runs on its own schedule, from the gateway, whether or not this console — or
any console — is open.

These are Curie's own cron jobs, in Curie's own store. A task made here is the
same thing `curie cron create` makes, fires on the same clock, and shows up in
`curie cron list`; a task made anywhere else shows up here. There is no
separate list.

### Making one

**NEW** opens the editor. The prompt is what the agent is asked to do, and it
has to stand on its own — every run is a fresh conversation with no prior
context, which is what makes a task reproducible.

The schedule is built from controls rather than typed, and there are three
ways to say when:

| Mode | For | Examples |
|------|-----|----------|
| **EVERY** | a countdown from now | `45s`, `30m`, `2h`, `3d` |
| **AT** | a wall clock | every day at `07:30`, weekdays at `09:00`, Mondays at `19:32`, once on a date |
| **CRON** | anything neither of those can say | `*/15 * * * *`, `0 9 * * 1-5` |

Under the controls is the schedule in words with its next fire time worked
out — `every 45s · until stopped · first run in 44s`. That line is the check:
the controls are the *inputs* to a schedule, and several of them are inert
depending on the mode, so one sentence saying when it will actually fire is
the only way to be sure before saving.

**REPEATING** off makes it a one-time task: the same number and unit, but
`in 2h` instead of `every 2h`. **RUNS** caps a repeating task at a number of
fires; `0` means until you stop it.

The rest is optional: a **MODEL** to pin this task to, where its output is
**DELIVER**ed, and a **FOLDER** to run it in (which is what makes the task see
that directory's `AGENTS.md` and gives its tools that working directory).

Intervals shorter than a minute work, and the ticker speeds up to match: with
a sub-minute task in the store it polls at that task's cadence instead of once
a minute. Five seconds is the floor — every run builds an agent and makes at
least one model call, so anything faster would start a run before the last one
finished.

### The list

| Column | Says |
|--------|------|
| **STATE** | `● ARMED`, `▶ RUNNING`, `‖ PAUSED`, `✓ DONE`, `✗ FAULT` |
| **TASK** | its name |
| **WHEN** | its schedule, as it was written |
| **NEXT** | how long until it fires |
| **LAST RUN** | how long ago it last did, and how that went |
| **RUNS** | how many times it has run, against its cap |

Everything is relative — `in 4m`, `12s ago` — because every question about a
scheduled task is. The columns shrink to fit: the schedule and the run tally
drop out before the name does.

The line under the table says whether anything is actually going to fire these.
A store full of perfectly good tasks and no gateway running is a page of things
that will never happen, and that is worth knowing now rather than at 2am:

```
Scheduler live · last tick 12s ago.
No scheduler running — nothing fires. Start:  curie gateway install
```

**RUN NOW** brings a task's next occurrence forward to the next tick rather
than running it here — a task belongs to the scheduler, and running it in this
process would give it the console's environment and no fire claim.
**PAUSE** and **DELETE** do what they say; DELETE asks twice, because one press
cannot be taken back.

### The task window

**WINDOW** opens a small chat onto a task's most recent run. It docks under the
list rather than floating over it, so the list is still there while you read;
**SIZE** trades rows between the two, and **CLOSE** gives them all back.

Every fire is its own conversation, so there is a real chat to open: the
messages of that run, live while it is happening. **◂ OLDER** and **NEWER ▸**
step back through previous runs of the same task.

It has its own composer, and it sends into *that* conversation — not into the
bench. So a run that ended somewhere useful can be carried on by hand: ask it
a follow-up, and the answer is written into the task's own session.

**STOP** ends the run wherever it is actually happening. A scheduled run
belongs to the gateway, which is a different process, so STOP leaves it a
request that it picks up within a couple of seconds and unwinds through the
same path it uses when the gateway shuts down under it. A turn you started in
the window belongs to this process and is interrupted directly. Typing `stop`
into the window's composer does the same thing, as do `run` and `close`.

While the scheduler is running a task, the window will not send into it: a run
holds a durable lease on its session and a second turn would wait behind it,
for up to half an hour, with nothing on screen to say so. It says that instead,
and offers STOP.

### From more than one window

The count beside SCHEDULE on the rail — and beside `^T TASKS` on the key line —
is how many tasks are running right now. It is there so that a task starting
while you are on another pane is visible without going to look.

Two consoles open at once agree with each other. Both are reading one store, so
a task made in one appears in the other within a couple of seconds, along with
anything else they share: the skin, the display mode, the indicator set, and
the logbook.

## Thinking and tool calls

A turn's reasoning and its tool calls are collected into a single **WORKINGS**
fold, shut by default. They are how an answer got made, not the answer, and
inline they bury the reply under screens of thinking.

Beside the shut fold, an indicator moves while the workings run and parks flat
when the answer starts. That is the whole indicator: motion means the model is
working, a parked trace means it is done — and the figure it draws is
different for reasoning than for a tool call, so a long wait says which one it
is without being opened. The heading counts tool calls once any have run, and
says nothing else — the reasoning text is the model talking to itself rather
than to you, and a running character count only tells you a number is going up.

Opening the fold does not cost you your place: the transcript keeps following
its newest line as reasoning streams into an expanded fold, so watching the
thinking never means scrolling down after it.

Click the fold, or focus it and press Enter, to read the whole run in order,
reasoning included. Nothing is hidden; it is just not in the way. A turn that
needed no tools and produced no reasoning leaves no fold behind.

Replies and errors are never folded.

## Starting over

`F12` starts a new conversation: the bench clears and the next turn opens a
new session. The agent is rebuilt rather than reset, so the new conversation
really is separate — its own session id, its own row in the logbook.

`Ctrl+L` only clears the screen. The conversation carries on.

## Going back over a turn

Two controls, side by side on the key line and clickable like every other cap.

**`Ctrl+G` — AGAIN** asks the last request again. The exchange is removed from
the conversation first and the same words are sent from a clean slate, so the
model answers without its previous attempt in context. Regenerating *with* the
old answer still in the history asks a different question — "improve on this" —
which is not what the key says it does.

Both are control keys rather than function keys: F11 is the fullscreen
toggle in essentially every terminal emulator, so it never reaches the
application, and a keycap advertising a key that cannot arrive is worse than
none.

**`Ctrl+B` — BACK** takes back one message: the newest thing you asked, and
everything the agent said in reply to it. The words go back into the composer
rather than vanishing, because the reason to go back one message is almost
always to change it. The session store is rewound to match, so the logbook and
the bench do not disagree.

Both are refused while a turn is running — `Ctrl+C` stops it first.

## The instruments

Four readouts, all fed from numbers the console actually has. Nothing here
displays an invented value — a pane that cannot answer says so instead.

**STATE** — what the console is doing, named, and drawn as a moving figure.
A turn spends most of its time in one of four states that look identical from
the outside unless each is reported separately: waiting on the provider,
reasoning, running a tool, and writing the answer. Two more are events rather
than conditions and borrow the instrument for a moment before giving it back —
a write to the record (a session being named, a conversation compressed) and a
fault. Which figures it draws depends on the [indicator set](#indicator-sets).

**OUTPUT** — a strip-chart recorder tracing characters per second as the reply
streams, with reasoning, tool calls and answer text on separate channels. One
undifferenced trace answers "is anything happening", which you can already
see; three answer the question you actually have — whether the last twenty
seconds went into thinking, into tools, or into the answer. The three share
one scale, so equal bursts draw equal heights, and each has its own glyph as
well as its own colour so the chart still reads on a monochrome terminal.

**CONTEXT** — a needle gauge over the model's context window. The needle's
colour bands at 75% and 90%, so "getting full" is visible before it is a
problem.

**TURN** — a shaded tape showing elapsed time on the running turn, plus a
character count.

Above them, the title bar carries four lamps: mains, an agent loaded, a turn
running, and a record lamp that pulses and fades whenever something is written
to the session store.

## DOS mode

The console has two skins. The default is the instrument panel described
above. The other is a phosphor terminal — one hue on dark glass, CP437 frames,
inverse-video bands, and the function-key bar every text-mode program of the
period had along the bottom of the screen.

Turn it on with the **DOS MODE** switch at the top of the PANEL pane (`F6`),
or from the CLI:

```bash
curie config set ui.skin_mode dos
```

It is a skin, not a second program. Every key does what it did, every pane is
where it was, the folded workings still fold, and the instruments read the
same numbers — what changes is the glass they are drawn on:

| | Bench panel | DOS mode |
|-|-------------|----------|
| Title bar | Two rows: wordmark, model, lamps, clock, a heavy rule under | One row of inverse video, with the long date |
| Rail | Stencilled switches with glyphs | A numbered menu box: the number is the key that throws it |
| Key line | `F1 HELP  F2 BENCH …` | `1HELP 2BENCH …` — the digit plain, the word on a lit band |
| Panes | A heading inside the region | A double-line frame with its name set into the top rule |
| Speakers | `▶ YOU`, `▮ CURIE`, `✗ FAULT` | `► YOU`, `■ CURIE`, `‼ FAULT` — CP437 only |
| Signals | Signal colours | Intensity and inverse video, because a monochrome tube had nothing else |

### Its settings

All four are on the PANEL pane under the switch, all four take effect
immediately, and all four are written to `config.yaml` — so the console opens
the way you left it.

| Setting | Key | What it does |
|---------|-----|--------------|
| Phosphor | `ui.dos.phosphor` | Which tube: `amber` (P3, the 1980s office), `green` (P1, the older tube), `white` (P4, paper-white) |
| Glow | `ui.dos.glow` | The brightness control, `0`–`4`: OFF, LOW, NORMAL, HIGH, BURN |
| Scanlines | `ui.dos.scanlines` | Draw every other raster line dark, in the chrome and in the animated figures |
| Block cursor | `ui.dos.block_cursor` | Blink the composer's cursor, as a DOS prompt's did |

**Glow is a real effect rather than a filter.** A CRT's lit pixels bloom into
the dark around them and the glass never returns to black, so the control does
two measurable things at once: it mixes the phosphor into the ground (the
haze) and drives the strokes toward the tube's peak (the bloom). Wind it up
and the picture gets brighter *and* softer — which is why the pane shows all
five steps at once, each drawn with the palette it actually resolves to, and
why every step is held to the same WCAG contrast floor the skin bridge uses.
BURN is meant to look over-driven; it is not allowed to become unreadable.

The animated [indicator sets](#indicator-sets) pick the mode up too: bloom
walks a lit cell up its density ramp and scanlines darken alternate rows, so
every set is drawn on the tube rather than merely re-coloured for it. The mode
also ships a set of its own — `raster`, a scanning spot with phosphor
persistence — and adopts it the first time you switch modes, unless you have
already chosen a set yourself.

### What it does not change

`display.skin` still dresses the CLI and the TUI while DOS mode is on, and
takes this console back the moment the mode is switched off. The two settings
are deliberately separate keys: one names the colour scheme Curie shares
across its interfaces, the other names which interface this console draws.

## Indicator sets

Every animated indicator in the console — the state instrument and the
thinking indicator beside a fold — draws from one **set**, chosen on the PANEL
pane beside the skin, previewed live before you pick it. Each set draws all
of the states, in one visual language, built on different mathematics:

| Set | Draws |
|-----|-------|
| `bench` | **Interference** — moiré beats between two sine gratings. The console's native ramp. |
| `scope` | **Lissajous** — two-axis oscillator figures; the frequency ratio names the state. |
| `cells` | **Automata** — elementary Wolfram rules; each state is a different class of behaviour. |
| `orbit` | **Phyllotaxis** — seed spirals; the divergence angle sets the arm count. |
| `sweep` | **Sweep** — a rotating radar beam with phosphor decay and fixed returns. |
| `bars` | **Spectrum** — a partial bank; the beat pattern names the state. |
| `raster` | **CRT raster** — a scanning spot with phosphor decay; persistence is the variable. [DOS mode](#dos-mode)'s own figure. |

The choice is remembered in `ui.indicators`, and can be set from the CLI:

```bash
curie config set ui.indicators scope
```

A set emits glyphs and palette *roles*, never colours, so it re-themes with
the skin, and it is drawn to whatever box it is given — the same set fills a
one-row strip beside a fold and a seven-row panel in the instrument stack. The
display's own optics are applied afterwards, to the finished figure, so every
set picks up [DOS mode](#dos-mode)'s glow and scanlines without a line of
per-set work.

## Voice

Two switches on the PANEL pane, both remembered between runs.

**MICROPHONE** (`ui.voice_input`) opens the microphone and dictates into the
composer. What you say is sent as a turn; if a turn is already running the
words are left in the composer rather than dropped, so you can send them when
the bench is free.

**SPEAKER** (`ui.speak_replies`) reads finished replies aloud through the
configured [TTS provider](./features/tts.md).

Below the switches, the pane lists every [Piper](./features/tts.md) voice
model in the voice folder, with its size and whether its metadata file is
there — a half-finished download is listed as *not ready* rather than being
silently unusable. Selecting one sets `tts.piper.voice` and makes Piper the
TTS provider, since the folder is Piper's.

**REFRESH VOICES** re-reads the folder. It exists because the folder changes
while the console is open: drop a `.onnx` model (and its `.onnx.json`) in and
press it, and the voice appears without a restart. The folder is
`~/.curie/cache/piper-voices/` unless `tts.piper.voices_dir` says otherwise;
the pane prints the path it is actually reading.

Voice needs the audio extra. Without it the switches say so rather than
failing:

```bash
curie update --ensure voice
```

## Sizing

The console reflows continuously and collapses chrome in a fixed order as the
window narrows:

| Width | What changes |
|-------|--------------|
| under ~122 columns | The key line drops its outer tiers, a few caps at a time |
| under ~100 columns | The instrument stack hides (a readout, not a control) |
| under ~96 columns | Speakers' names in the transcript shrink to their marks |
| under ~84 columns | The rail keeps its switches but drops their labels |
| under ~58 columns | The rail hides; the function-key line says so |

A dropped keycap is a dropped *reminder*: every key it advertised still works
at every width. `F1` and `F10` are never dropped, because they are the two
that get you out of a state you did not mean to enter.

The transcript and the composer are never collapsed — they are the reason the
console exists.

## Polarity

The palette has a light half and a dark half. Which one is used is resolved
in this order:

1. `CURIE_UI_POLARITY=light` or `=dark`, or `curie ui --polarity light|dark`
2. `COLORFGBG`, which xterm-family terminals set as `fg;bg` ANSI indices
3. Light, because that is what the shipped palette is authored for

There is no portable way to ask a terminal what colour its background is, so
if the guess is wrong, set it explicitly.

## Requirements

The console needs the `ui` extra, which supplies [Textual](https://textual.textualize.io/).
Both installers include it by default, and both then check that it actually
landed and install it directly if the resolution fell short. [DOS mode](#dos-mode)
needs nothing beyond it — the whole skin is the standard library plus what the
console already had. If you installed without extras:

```bash
curie update --ensure ui
# or
pip install 'curie-agent[ui]'
```

It needs a real terminal — it draws a full screen and reads mouse events,
neither of which survives a pipe or a redirect. For scripted use,
`curie -z '<prompt>'` prints one answer and exits.

Windows Terminal, cmd.exe, PowerShell, macOS Terminal, iTerm2 and the common
Linux terminals all report mouse events. Over SSH it works as long as your
client passes them through.

## Related

- [CLI Usage](./cli.md) — the classic prompt
- [TUI](./tui.md) — the keyboard-driven interface
- [Skins & Themes](./features/skins.md) — what the console is themed by
- [SOUL.md](./features/soul.md) — how the agent writes
