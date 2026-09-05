---
sidebar_position: 10
title: "Skins & Themes"
description: "Customize the Curie CLI with built-in and user-defined skins"
---

# Skins & Themes

Skins control the **visual presentation** of Curie: banner colours, spinner
faces and verbs, response-box labels, branding text, and the tool activity
prefix. One skin themes all three surfaces at once — the classic CLI, the TUI,
and the [bench console](#the-bench-console) — so switching is a single move.

How Curie *writes* and how Curie *looks* are separate settings:

- **[SOUL.md](./soul.md)** is the agent's voice — tone and wording.
- **Skin** is its appearance.

## Change skins

```bash
/skin                # show the current skin and list available skins
/skin curie-vga      # switch to a built-in skin
/skin mytheme        # switch to a custom skin from ~/.curie/skins/mytheme.yaml
```

Or set the default skin in `~/.curie/config.yaml`:

```yaml
display:
  skin: default
```

## Built-in skins

The shipped default is **the bench**: a mid-century instrument panel rendered
in a terminal. Enamel ground, graphite ink, signal colour off a warning
placard, chrome drawn with the box and shading glyphs of IBM code page 437 —
four densities, a handful of line weights, which is what a text-mode program
had to build an interface out of.

It is authored for a **light terminal**. Every text colour clears 3:1 against
white and body ink sits at 14.4:1, so it stays readable on the silver-white
background most terminals ship with. Each of the paired skins also carries a
hand-tuned block for the opposite polarity, so a dark terminal gets the same
panel under a lamp rather than an automatic inversion.

| Skin | Description | Visual character |
|------|-------------|------------------|
| `default` | Curie bench — warm enamel and stencilled signal colour | The shipped look. Burnt amber and oxide red on enamel silver-white, brass trim, graphite body text. Spinner verbs are bench work: "calibrating", "weighing out", "cross-checking", "reading the dial". Banner hero is a strip-chart recorder trace. |
| `curie-nightbench` | The same panel under the lamp | The bench palette's dark half promoted to a skin of its own, for anyone who wants dark regardless of what the terminal reports. |
| `curie-vga` | The IBM VGA sixteen | Not "inspired by" — the exact sixteen colours a VGA card put on the wire for its default text palette. Turbo Vision blue, stencil white, double-line chrome. The status bar sits on black because light red and light magenta only clear 3:1 there, and a DOS status line was drawn as the bright half of the palette on a dark cell for exactly that reason. |
| `curie-amber` | Amber phosphor | One hue for the whole interface, with hierarchy from brightness alone — which is all an IBM 5151-class amber monitor had to give, and why amber terminals stay legible at any size. |
| `curie-blueprint` | Cyanotype | An engineering drawing reproduced by contact print. Prussian ink on drafting paper in the light, white lines on Prussian blue in the dark. |
| `mono` | Monochrome — clean grayscale | All grays, no colour. Ideal for minimal setups or screen recordings. |
| `slate` | Cool blue — developer-focused | Royal blue borders, soft blue text. Calm and professional. |
| `daylight` | Light theme with cool blue accents | Dark slate text with blue borders and pale status surfaces, for white or bright terminals. |
| `warm-lightmode` | Warm brown/gold for light backgrounds | Warm parchment tones — dark brown text, saddle-brown accents, cream status surfaces. An earthier alternative to `daylight`. |

:::note Retired skins
`ares`, `poseidon`, `sisyphus` and `charizard` are gone. They were branded to
the agent's previous identity — Hermes's own pantheon, plus a mascot joke on
top of it. `mono`, `slate`, `daylight` and `warm-lightmode` stay: they
describe a terminal polarity, not an identity.
:::

## The bench console

`curie ui` opens a full-screen, mouse-driven console themed by the active
skin. Clicking a skin in its **PANEL** pane applies it everywhere — the
console repaints, and the CLI and TUI pick it up too.

```bash
curie ui                    # follow the terminal's own polarity hints
curie ui --polarity dark    # force the dark half of the palette
curie ui --polarity light   # force the light half
```

Polarity is resolved from `CURIE_UI_POLARITY`, then `COLORFGBG` (which
xterm-family terminals set as `fg;bg` ANSI indices), then a light default.

### Contrast is guaranteed, not assumed

The console owns its own window ground, which the CLI does not — in the CLI
most colours are painted on whatever background the user's terminal happens
to have. That difference matters, because unset skin keys inherit from the
default skin: a dark-authored skin arrives carrying the bench's *light*
background alongside its own *light* foreground, and every word would land at
1.05:1.

So the console measures instead of trusting. It derives the window ground from
the skin's own foreground rather than from an inherited `background` key, then
checks every role against the ground it will actually be painted on and moves
its lightness — holding hue and saturation — until it clears WCAG AA: 4.5:1
for body text, 3:1 for everything else, including borders and rules.

A skin authored to pass comes through untouched. A skin whose colours do not
combine is made readable rather than rendered as-is, because "the theme is
unreadable" is not something a user can debug from the outside. This applies
to your own skins too — you do not have to hand-tune a palette for the
console to be usable in it.

## Wordmark scaling

The banner wordmark comes in three sizes and the widest that fits is chosen,
measured from the art itself:

| Terminal width | What renders |
|----------------|--------------|
| 86 columns or more | `CURIE-AGENT` in full block letters |
| 36–85 columns | `CURIE` alone, same block letters |
| 20–35 columns | A single stencilled rule |
| under 20 columns | Nothing — every row is worth more as content |

A custom skin can supply `banner_logo`, `banner_logo_medium` and
`banner_logo_compact`; any it omits falls through to the next size down.
Widths are measured from the art, so a narrower or wider wordmark is sized
correctly without declaring anything.

## Complete list of configurable keys

### Colors (`colors:`)

Controls every colour value across the CLI, the TUI, and the bench
console. Values are hex colour strings.

The defaults below are the bench palette, which is authored for a light
terminal. A skin may also supply `light_colors:` or `dark_colors:` with
the same keys — a hand-tuned block for the opposite polarity, merged over
`colors:` rather than replacing it, so a partial block still resolves to
a complete palette.

| Key | Description | Default (`default` skin) |
|-----|-------------|--------------------------|
| `banner_border` | Panel border around the startup banner | `#8C7A5B` (brass trim) |
| `banner_title` | Title text color in the banner | `#8A3B12` (stencilled lettering) |
| `banner_accent` | Section headers in the banner (Available Tools, etc.) | `#B4541A` (signal amber) |
| `banner_dim` | Muted text in the banner (separators, secondary labels) | `#6E675C` (warm graphite) |
| `banner_text` | Body text in the banner (tool names, skill names) | `#2B2A27` (graphite ink) |
| `ui_accent` | General UI accent color (highlights, active elements) | `#B4541A` |
| `ui_label` | UI labels and tags | `#8A6A21` (brass) |
| `ui_ok` | Success indicators (checkmarks, completion) | `#2F6B33` (lab green) |
| `ui_error` | Error indicators (failures, blocked) | `#A32218` (hazard red) |
| `ui_warn` | Warning indicators (caution, approval prompts) | `#B07105` (caution amber) |
| `prompt` | Interactive prompt text color | `#2B2A27` |
| `input_rule` | Horizontal rule above the input area | `#B39A72` |
| `response_border` | Border around the agent's response box (ANSI escape) | `#B4541A` |
| `session_label` | Session label color | `#8A6A21` |
| `session_border` | Session ID dim border color | `#A89C88` |
| `status_bar_bg` | Background color for the TUI status / usage bar | `#EDE8DE` |
| `voice_status_bg` | Background color for the voice-mode status badge | `#EDE8DE` |
| `selection_bg` | Background color for the TUI mouse-selection highlighter. Falls back to `completion_menu_current_bg` when unset. | `#E4D9C6` |
| `completion_menu_bg` | Background color for the completion menu list | `#EFEAE0` |
| `completion_menu_current_bg` | Background color for the active completion row | `#E0D6C4` |
| `completion_menu_meta_bg` | Background color for the completion meta column | `#E9E3D7` |
| `completion_menu_meta_current_bg` | Background color for the active completion meta column | `#D6C9B2` |

### Spinner (`spinner:`)

Controls the animated spinner shown while waiting for API responses.

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `waiting_faces` | list of strings | Faces cycled while waiting for API response | `["[▖]", "[▘]", "[▝]", "[▗]"]` (a quadrant sweep, like a dial hand) |
| `thinking_faces` | list of strings | Faces cycled during model reasoning | `["[░]", "[▒]", "[▓]", "[█]"]` (the CGA density ramp) |
| `thinking_verbs` | list of strings | Verbs shown in spinner messages | `["calibrating", "weighing out", "cross-checking", "reading the dial"]` |
| `wings` | list of [left, right] pairs | Decorative brackets around the spinner | `[["╢", "╟"], ["◄", "►"], ["▐", "▌"]]` |

When spinner values are empty (as in `mono` and `slate`), the hardcoded
defaults in `display.py` are used.

### Branding (`branding:`)

Text strings used throughout the CLI interface.

| Key | Description | Default |
|-----|-------------|---------|
| `agent_name` | Name shown in banner title and status display | `Curie Agent` |
| `welcome` | Welcome message shown at CLI startup | `CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.` |
| `goodbye` | Message shown on exit | `Bench secured. Logbook closed. ▮` |
| `response_label` | Label on the response box header | ` ▮ CURIE ` |
| `prompt_symbol` | Symbol before the user input prompt (bare token, renderers add a trailing space) | `▶` |
| `help_header` | Header text for the `/help` command output | `▮ COMMAND INDEX` |

### Other top-level keys

| Key | Type | Description | Default |
|-----|------|-------------|---------|
| `tool_prefix` | string | Character prefixed to tool output lines in the CLI | `│` |
| `tool_emojis` | dict | Per-tool emoji overrides for spinners and progress (`{tool_name: emoji}`) | `{}` |
| `banner_logo` | string | Rich-markup wordmark, full width (86 columns for the built-in) | `""` |
| `banner_logo_medium` | string | Wordmark for narrower windows; falls back to `banner_logo` | `""` |
| `banner_logo_compact` | string | Wordmark for narrow windows; below its width nothing is drawn | `""` |
| `banner_hero` | string | Rich-markup hero art beside the startup panel, ~30 columns | `""` |

## Custom skins

Create YAML files under `~/.curie/skins/`. User skins inherit missing values from the built-in `default` skin, so you only need to specify the keys you want to change.

### Full custom skin YAML template

```yaml
# ~/.curie/skins/mytheme.yaml
# Complete skin template — all keys shown. Delete any you don't need;
# missing values automatically inherit from the 'default' skin.

name: mytheme
description: My custom theme

colors:
  banner_border: "#CD7F32"
  banner_title: "#FFD700"
  banner_accent: "#FFBF00"
  banner_dim: "#B8860B"
  banner_text: "#FFF8DC"
  ui_accent: "#FFBF00"
  ui_label: "#4dd0e1"
  ui_ok: "#4caf50"
  ui_error: "#ef5350"
  ui_warn: "#ffa726"
  prompt: "#FFF8DC"
  input_rule: "#CD7F32"
  response_border: "#FFD700"
  session_label: "#DAA520"
  session_border: "#8B8682"
  status_bar_bg: "#1a1a2e"
  voice_status_bg: "#1a1a2e"
  selection_bg: "#333355"
  completion_menu_bg: "#1a1a2e"
  completion_menu_current_bg: "#333355"
  completion_menu_meta_bg: "#1a1a2e"
  completion_menu_meta_current_bg: "#333355"

spinner:
  waiting_faces:
    - "(⚔)"
    - "(⛨)"
    - "(▲)"
  thinking_faces:
    - "(⚔)"
    - "(⌁)"
    - "(<>)"
  thinking_verbs:
    - "processing"
    - "analyzing"
    - "computing"
    - "evaluating"
  wings:
    - ["⟪⚡", "⚡⟫"]
    - ["⟪●", "●⟫"]

branding:
  agent_name: "My Agent"
  welcome: "Welcome to My Agent! Type your message or /help for commands."
  goodbye: "See you later! ⚡"
  response_label: " ⚡ My Agent "
  prompt_symbol: "⚡"
  help_header: "(⚡) Available Commands"

tool_prefix: "┊"

# Per-tool emoji overrides (optional)
tool_emojis:
  terminal: "⚔"
  web_search: "🔮"
  read_file: "📄"

# Custom ASCII art banners (optional, Rich markup supported).
#
# The wordmark has three sizes; the widest that fits the terminal is used and
# any you omit falls through to the next one down. Widths are measured from
# the art itself, so you do not declare them. Give the compact size a real
# value if you care about narrow windows — under its width, no wordmark is
# drawn at all, which is deliberate.
# banner_logo: |
#   [bold #8A3B12] MY AGENT IN FULL BLOCK LETTERS [/]
# banner_logo_medium: |
#   [bold #8A3B12] SHORTER [/]
# banner_logo_compact: |
#   [bold #8A3B12]╒═ M Y   A G E N T ═╕[/]
#
# banner_hero is the art beside the startup panel — roughly 30 columns wide.
# banner_hero: |
#   [#8A3B12]  Custom art here  [/]
```

### Minimal custom skin example

Since everything inherits from `default`, a minimal skin only needs to change what's different:

```yaml
name: cyberpunk
description: Neon terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

## Curie Mod — Visual Skin Editor

[Curie Mod](https://github.com/cocktailpeanut/curie-mod) is a community-built web UI for creating and managing skins visually. Instead of writing YAML by hand, you get a point-and-click editor with live preview.

![Curie Mod skin editor](https://raw.githubusercontent.com/cocktailpeanut/curie-mod/master/nous.png)

**What it does:**

- Lists all built-in and custom skins
- Opens any skin into a visual editor with all Curie skin fields (colors, spinner, branding, tool prefix, tool emojis)
- Generates `banner_logo` text art from a text prompt
- Converts uploaded images (PNG, JPG, GIF, WEBP) into `banner_hero` ASCII art with multiple render styles (braille, ASCII ramp, blocks, dots)
- Saves directly to `~/.curie/skins/`
- Activates a skin by updating `~/.curie/config.yaml`
- Shows the generated YAML and a live preview

### Install

**Option 1 — Pinokio (1-click):**

Find it on [pinokio.computer](https://pinokio.computer) and install with one click.

**Option 2 — npx (quickest from terminal):**

```bash
npx -y curie-mod
```

**Option 3 — Manual:**

```bash
git clone https://github.com/cocktailpeanut/curie-mod.git
cd curie-mod/app
npm install
npm start
```

### Usage

1. Start the app (via Pinokio or terminal).
2. Open **Skin Studio**.
3. Choose a built-in or custom skin to edit.
4. Generate a logo from text and/or upload an image for hero art. Pick a render style and width.
5. Edit colors, spinner, branding, and other fields.
6. Click **Save** to write the skin YAML to `~/.curie/skins/`.
7. Click **Activate** to set it as the current skin (updates `display.skin` in `config.yaml`).

Curie Mod respects the `CURIE_HOME` environment variable, so it works with [profiles](/user-guide/profiles) too.

## Operational notes

- Built-in skins load from `curie_cli/skin_engine.py`.
- Unknown skins automatically fall back to `default`.
- `/skin` updates the active CLI theme immediately for the current session.
- User skins in `~/.curie/skins/` take precedence over built-in skins with the same name.
- Skin changes via `/skin` are session-only. To make a skin your permanent default, set it in `config.yaml`.
- The `banner_logo` and `banner_hero` fields support Rich console markup (e.g., `[bold #FF0000]text[/]`) for colored ASCII art.
