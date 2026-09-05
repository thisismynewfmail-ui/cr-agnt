"""Curie skin/theme engine — the theme SDK for every surface.

A data-driven skin system that lets users (and Curie itself) customize the
visual appearance across the CLI, the TUI, and the desktop GUI from a single
file. Skins are defined as YAML files in ~/.curie/skins/ or as built-in presets.
No code changes are needed to add a new skin.

This module is the source of truth: it resolves the active skin, and the gateway
pushes the resolved palette to the TUI and desktop (see tui_gateway's
``resolve_skin`` / ``skin.changed``). A skin dropped in ~/.curie/skins/ therefore
themes all three surfaces at once — the theme analogue of the plugin SDK.

SKIN YAML SCHEMA
================

All fields are optional. Missing values inherit from the ``default`` skin.

.. code-block:: yaml

    # Required: skin identity
    name: mytheme                         # Unique skin name (lowercase, hyphens ok)
    description: Short description        # Shown in /skin listing

    # Colors: hex values for Rich markup (banner, UI, response box)
    colors:
      background: "#0e0e12"               # App/base surface — the seed the TUI
                                          # status bar and the desktop GUI derive
                                          # their whole palette from (see below).
      banner_border: "#CD7F32"            # Panel border color
      banner_title: "#FFD700"             # Panel title text color
      banner_accent: "#FFBF00"            # Section headers (Available Tools, etc.)
      banner_dim: "#B8860B"               # Dim/muted text (separators, labels)
      banner_text: "#FFF8DC"              # Body text (tool names, skill names)
      ui_accent: "#FFBF00"               # General UI accent
      ui_label: "#DAA520"                # UI labels (warm gold; teal clashed w/ default banner gold)
      ui_ok: "#4caf50"                   # Success indicators
      ui_error: "#ef5350"                # Error indicators
      ui_warn: "#ffa726"                 # Warning indicators
      ui_tool: "#FFBF00"                 # Tool-call markers (● / spinner); falls back to ui_accent
      ui_thinking: "#CC9B1F"             # Reasoning/thinking text; falls back to banner_dim
      diff_added: "#dcffdc"              # Diff added-line background (TUI)
      diff_removed: "#ffdcdc"            # Diff removed-line background
      diff_added_word: "#248a3d"         # Diff added word-level foreground
      diff_removed_word: "#cf222e"       # Diff removed word-level foreground
      syntax_string: "#FFBF00"           # Code strings; falls back to ui_accent
      syntax_number: "#FFF8DC"           # Code numbers; falls back to ui_text
      syntax_keyword: "#CD7F32"          # Code keywords; falls back to ui_border
      syntax_comment: "#CC9B1F"          # Code comments; falls back to banner_dim
      prompt: "#FFF8DC"                  # Prompt text color
      input_rule: "#CD7F32"              # Input area horizontal rule
      response_border: "#FFD700"         # Response box border (ANSI)
      status_bar_bg: "#1a1a2e"           # Status bar background
      status_bar_text: "#C0C0C0"         # Status bar default text
      status_bar_strong: "#FFD700"       # Status bar highlighted text
      status_bar_dim: "#8B8682"          # Status bar separators/muted text
      status_bar_good: "#8FBC8F"         # Healthy context usage
      status_bar_warn: "#FFD700"         # Warning context usage
      status_bar_bad: "#FF8C00"          # High context usage
      status_bar_critical: "#FF6B6B"     # Critical context usage
      session_label: "#DAA520"           # Session label color
      session_border: "#8B8682"          # Session ID dim color
      status_bar_bg: "#1a1a2e"          # TUI status/usage bar background
      voice_status_bg: "#1a1a2e"        # TUI voice status background
      selection_bg: "#333355"           # TUI mouse-selection highlight background
      completion_menu_bg: "#1a1a2e"      # Completion menu background
      completion_menu_current_bg: "#333355"  # Active completion row background
      completion_menu_meta_bg: "#1a1a2e"     # Completion meta column background
      completion_menu_meta_current_bg: "#333355"  # Active completion meta background

    # Optional paired palette for the opposite terminal polarity (mirrors the
    # desktop app's colors/darkColors pairing). If `colors` above is authored
    # for dark terminals, `light_colors` supplies the hand-tuned light-terminal
    # variant (same keys); light-authored skins supply `dark_colors` instead.
    # Without a paired block, the TUI adapts `colors` automatically
    # (contrast-clamped foregrounds, polarity-corrected fills).
    light_colors:
      banner_title: "#8B6914"
      # ... same keys as `colors` ...

    # Spinner: customize the animated spinner during API calls
    spinner:
      waiting_faces:                      # Faces shown while waiting for API
        - "(⚔)"
        - "(⛨)"
      thinking_faces:                     # Faces shown during reasoning
        - "(⌁)"
        - "(<>)"
      thinking_verbs:                     # Verbs for spinner messages
        - "forging"
        - "plotting"
      wings:                              # Optional left/right spinner decorations
        - ["⟪⚔", "⚔⟫"]                  # Each entry is [left, right] pair
        - ["⟪▲", "▲⟫"]

    # Branding: text strings used throughout the CLI
    branding:
      agent_name: "Curie Agent"          # Banner title, status display
      welcome: "Welcome message"          # Shown at CLI startup
      goodbye: "Goodbye! ▮"              # Shown on exit
      response_label: " ▮ Curie "       # Response box header label
      prompt_symbol: "❯"                 # Input prompt symbol (bare token; renderers add trailing space)
      help_header: "(^_^)? Commands"      # /help header text

    # Tool prefix: character for tool output lines (default: ┊)
    tool_prefix: "┊"

    # Tool emojis: override the default emoji for any tool (used in spinners & progress)
    tool_emojis:
      terminal: "⚔"           # Override terminal tool emoji
      web_search: "🔮"        # Override web_search tool emoji
      # Any tool not listed here uses its registry default

USAGE
=====

.. code-block:: python

    from curie_cli.skin_engine import get_active_skin, list_skins, set_active_skin

    skin = get_active_skin()
    print(skin.colors["banner_title"])    # "#FFD700"
    print(skin.get_branding("agent_name"))  # "Curie Agent"

    set_active_skin("ares")               # Switch to built-in ares skin
    set_active_skin("mytheme")            # Switch to user skin from ~/.curie/skins/

BUILT-IN SKINS
==============

- ``default`` — Classic Curie gold/kawaii (the current look)
- ``ares``    — Crimson/bronze war-god theme with custom spinner wings
- ``mono``    — Clean grayscale monochrome
- ``slate``   — Cool blue developer-focused theme
- ``daylight`` — Light background theme with dark text and blue accents
- ``warm-lightmode`` — Warm brown/gold text for light terminal backgrounds

USER SKINS
==========

Drop a YAML file in ``~/.curie/skins/<name>.yaml`` following the schema above.
Activate with ``/skin <name>`` in the CLI or ``display.skin: <name>`` in config.yaml.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from curie_constants import get_curie_home

logger = logging.getLogger(__name__)


# =============================================================================
# Skin data structure
# =============================================================================

_MARKUP_TAG = re.compile(r"\[/?[^\[\]]*\]")


def _art_width(art: str) -> int:
    """Widest rendered line of a Rich-markup art block, in columns.

    Markup tags occupy no columns, so they are stripped before measuring.
    Every glyph used by the built-in art is single-width; a user skin that
    reaches for wide characters will measure short and simply pick a smaller
    size than it strictly needed, which fails safe.
    """
    return max(
        (len(_MARKUP_TAG.sub("", line)) for line in art.splitlines()),
        default=0,
    )


@dataclass
class SkinConfig:
    """Complete skin configuration."""
    name: str
    description: str = ""
    colors: Dict[str, str] = field(default_factory=dict)
    # Paired palettes for terminals whose background polarity differs from the
    # one `colors` was authored against (mirrors the desktop app's
    # colors/darkColors pairing). A consumer that knows the terminal is light
    # prefers `light_colors` (falling back to `colors`), and vice versa for
    # `dark_colors`. Both merge over the default skin's matching block, so
    # partial user skins still resolve to a complete palette.
    light_colors: Dict[str, str] = field(default_factory=dict)
    dark_colors: Dict[str, str] = field(default_factory=dict)
    spinner: Dict[str, Any] = field(default_factory=dict)
    branding: Dict[str, str] = field(default_factory=dict)
    tool_prefix: str = "┊"
    tool_emojis: Dict[str, str] = field(default_factory=dict)  # per-tool emoji overrides
    # Rich-markup wordmark at three widths. ``banner_logo`` is the full
    # CURIE-AGENT block; ``banner_logo_medium`` drops "AGENT" so the letters
    # still fit a 40-column window; ``banner_logo_compact`` is a stencilled
    # rule for anything narrower. A skin may supply only the widest — the
    # renderer falls back down the chain and finally to no art at all, which
    # is the right answer at 30 columns.
    banner_logo: str = ""
    banner_logo_medium: str = ""
    banner_logo_compact: str = ""
    banner_hero: str = ""    # Rich-markup hero art

    def get_color(self, key: str, fallback: str = "") -> str:
        """Get a color value with fallback."""
        return self.colors.get(key, fallback)

    def get_spinner_wings(self) -> List[Tuple[str, str]]:
        """Get spinner wing pairs, or empty list if none."""
        raw = self.spinner.get("wings", [])
        result = []
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                result.append((str(pair[0]), str(pair[1])))
        return result

    def get_branding(self, key: str, fallback: str = "") -> str:
        """Get a branding value with fallback."""
        return self.branding.get(key, fallback)

    def logo_for_width(self, columns: int) -> str:
        """Return the widest wordmark that fits ``columns``, or "" if none do.

        Art wider than the terminal does not degrade gracefully: Rich wraps it
        mid-glyph and the wordmark arrives as three rows of debris. Measuring
        first and stepping down a size is the whole mechanism — and returning
        "" below the smallest size is a real answer, not a failure. A 30-column
        window has no room for a wordmark and every row it does have is worth
        more as content.

        Widths are measured from the art itself (markup stripped), so a user
        skin with narrower or wider art is sized correctly without declaring
        anything.
        """
        for art in (
            self.banner_logo,
            self.banner_logo_medium,
            self.banner_logo_compact,
        ):
            if art and _art_width(art) <= columns:
                return art
        return ""


# =============================================================================
# Built-in skin definitions
# =============================================================================

_BUILTIN_SKINS: Dict[str, Dict[str, Any]] = {
    "default": {
        "name": "default",
        "description": "Curie bench — warm enamel and stencilled signal colour",
        "colors": {
            "banner_border": "#8C7A5B",
            "banner_title": "#8A3B12",
            "banner_accent": "#B4541A",
            "banner_dim": "#6E675C",
            "banner_text": "#2B2A27",
            "ui_accent": "#B4541A",
            "ui_label": "#8A6A21",
            "ui_ok": "#2F6B33",
            "ui_error": "#A32218",
            "ui_warn": "#B07105",
            "ui_tool": "#8A3B12",
            "ui_thinking": "#7A6A46",
            "prompt": "#2B2A27",
            "input_rule": "#B39A72",
            "response_border": "#B4541A",
            "status_bar_bg": "#EDE8DE",
            "status_bar_text": "#2B2A27",
            "status_bar_strong": "#8A3B12",
            "status_bar_dim": "#8C8375",
            "status_bar_good": "#2F6B33",
            "status_bar_warn": "#B07105",
            "status_bar_bad": "#C0561A",
            "status_bar_critical": "#A32218",
            "session_label": "#8A6A21",
            "session_border": "#A89C88",
            "completion_menu_bg": "#EFEAE0",
            "completion_menu_current_bg": "#E0D6C4",
            "completion_menu_meta_bg": "#E9E3D7",
            "completion_menu_meta_current_bg": "#D6C9B2",
            "selection_bg": "#E4D9C6",
            "shell_dollar": "#B4541A",
            "voice_status_bg": "#EDE8DE",
            "diff_added": "#DEEBD8",
            "diff_removed": "#F2DCD8",
            "diff_added_word": "#2F6B33",
            "diff_removed_word": "#A32218",
            "syntax_string": "#8A6A21",
            "syntax_number": "#8A3B12",
            "syntax_keyword": "#8E2F14",
            "syntax_comment": "#8C8375",
        },
        "dark_colors": {
            "banner_border": "#8A6A3B",
            "banner_title": "#E8A05A",
            "banner_accent": "#E07B32",
            "banner_dim": "#9A8F7A",
            "banner_text": "#EFE7D8",
            "ui_accent": "#E07B32",
            "ui_label": "#D6B25E",
            "ui_ok": "#6FBF73",
            "ui_error": "#E5675C",
            "ui_warn": "#E0A63A",
            "ui_tool": "#E8A05A",
            "ui_thinking": "#BFA97A",
            "prompt": "#EFE7D8",
            "input_rule": "#7A6544",
            "response_border": "#E8A05A",
            "status_bar_bg": "#211E19",
            "status_bar_text": "#EFE7D8",
            "status_bar_strong": "#E8A05A",
            "status_bar_dim": "#7E7466",
            "status_bar_good": "#6FBF73",
            "status_bar_warn": "#E0A63A",
            "status_bar_bad": "#E07B32",
            "status_bar_critical": "#E5675C",
            "session_label": "#D6B25E",
            "session_border": "#6E6656",
            "completion_menu_bg": "#211E19",
            "completion_menu_current_bg": "#3B342A",
            "completion_menu_meta_bg": "#1D1B16",
            "completion_menu_meta_current_bg": "#332D24",
            "selection_bg": "#453C2E",
            "shell_dollar": "#E07B32",
            "voice_status_bg": "#211E19",
            "diff_added": "#1E2A1C",
            "diff_removed": "#2E1B18",
            "diff_added_word": "#6FBF73",
            "diff_removed_word": "#E5675C",
            "syntax_string": "#D6B25E",
            "syntax_number": "#E8A05A",
            "syntax_keyword": "#E07B32",
            "syntax_comment": "#7E7466",
        },
        "spinner": {
            "waiting_faces": ["[▖]", "[▘]", "[▝]", "[▗]"],
            "thinking_faces": ["[░]", "[▒]", "[▓]", "[█]", "[▓]", "[▒]"],
            "thinking_verbs": [
                "calibrating",
                "weighing out",
                "cross-checking",
                "collating",
                "indexing",
                "reading the dial",
                "tabulating",
                "running the assay",
                "checking the standard",
                "logging to the notebook",
                "measuring twice",
                "annealing",
                "developing the plate",
                "filing the record",
                "squaring the numbers",
                "reducing the sample",
            ],
            "wings": [
                ["╢", "╟"],
                ["◄", "►"],
                ["▐", "▌"],
                ["╡", "╞"],
            ],
        },
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.",
            "goodbye": "Bench secured. Logbook closed. ▮",
            "response_label": " ▮ CURIE ",
            "prompt_symbol": "▶",
            "help_header": "▮ COMMAND INDEX",
        },
        "tool_prefix": "│",
        "banner_logo": """[bold #8A3B12] ██████╗██╗   ██╗██████╗ ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #8A3B12]██╔════╝██║   ██║██╔══██╗██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#B4541A]██║     ██║   ██║██████╔╝██║█████╗  █████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   [/]
[#B4541A]██║     ██║   ██║██╔══██╗██║██╔══╝  ╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   [/]
[#8A6A21]╚██████╗╚██████╔╝██║  ██║██║███████╗      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   [/]
[#8C7A5B] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   [/]""",
        "banner_logo_medium": """[bold #8A3B12] ██████╗██╗   ██╗██████╗ ██╗███████╗[/]
[bold #8A3B12]██╔════╝██║   ██║██╔══██╗██║██╔════╝[/]
[#B4541A]██║     ██║   ██║██████╔╝██║█████╗  [/]
[#B4541A]██║     ██║   ██║██╔══██╗██║██╔══╝  [/]
[#8A6A21]╚██████╗╚██████╔╝██║  ██║██║███████╗[/]
[#8C7A5B] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝[/]""",
        "banner_logo_compact": """[bold #8A3B12]╒═══ C U R I E ═══╕[/]
[#8C7A5B]╘═ bench terminal ═╛[/]""",
        "banner_hero": """[#8C7A5B]╔════════════════════════════╗[/]
[#8C7A5B]║[/][bold #2B2A27] CURIE · STRIP CHART  No.4  [/][#8C7A5B]║[/]
[#8C7A5B]╠════════════════════════════╣[/]
[#8C7A5B]║[/][#8C7A5B]100┤[/]                        [#8C7A5B]║[/]
[#8C7A5B]║[/][#8C7A5B]   ┤[/][#B4541A]         ▗▄▖            [/][#8C7A5B]║[/]
[#8C7A5B]║[/][#8C7A5B] 50┤[/][#B4541A]    ▗▄▟▀▘   ▝▀▙▄▖       [/][#8C7A5B]║[/]
[#8C7A5B]║[/][#8C7A5B]   ┤[/][#B4541A] ▄▟▀▘          ▝▀▙▄▄▄   [/][#8C7A5B]║[/]
[#8C7A5B]║[/][#8C7A5B]  0┼────────────────────────[/][#8C7A5B]║[/]
[#8C7A5B]║[/][#8C7A5B]   0    5   10   15   20min [/][#8C7A5B]║[/]
[#8C7A5B]╠════════════════════════════╣[/]
[#8C7A5B]║[/] [#2F6B33]▉[/][#2B2A27] MAINS[/]  [#2F6B33]▉[/][#2B2A27] BENCH[/]  [#B07105]░[/][#2B2A27] LOG[/]   [#8C7A5B]║[/]
[#8C7A5B]╚════════════════════════════╝[/]""",
    },
    "curie-nightbench": {
        "name": "curie-nightbench",
        "description": "Curie bench after hours — the same panel under the lamp",
        "colors": {
            "background": "#191713",
            "banner_border": "#8A6A3B",
            "banner_title": "#E8A05A",
            "banner_accent": "#E07B32",
            "banner_dim": "#9A8F7A",
            "banner_text": "#EFE7D8",
            "ui_accent": "#E07B32",
            "ui_label": "#D6B25E",
            "ui_ok": "#6FBF73",
            "ui_error": "#E5675C",
            "ui_warn": "#E0A63A",
            "ui_tool": "#E8A05A",
            "ui_thinking": "#BFA97A",
            "prompt": "#EFE7D8",
            "input_rule": "#7A6544",
            "response_border": "#E8A05A",
            "status_bar_bg": "#211E19",
            "status_bar_text": "#EFE7D8",
            "status_bar_strong": "#E8A05A",
            "status_bar_dim": "#7E7466",
            "status_bar_good": "#6FBF73",
            "status_bar_warn": "#E0A63A",
            "status_bar_bad": "#E07B32",
            "status_bar_critical": "#E5675C",
            "session_label": "#D6B25E",
            "session_border": "#6E6656",
            "completion_menu_bg": "#211E19",
            "completion_menu_current_bg": "#3B342A",
            "completion_menu_meta_bg": "#1D1B16",
            "completion_menu_meta_current_bg": "#332D24",
            "selection_bg": "#453C2E",
            "shell_dollar": "#E07B32",
            "voice_status_bg": "#211E19",
            "diff_added": "#1E2A1C",
            "diff_removed": "#2E1B18",
            "diff_added_word": "#6FBF73",
            "diff_removed_word": "#E5675C",
            "syntax_string": "#D6B25E",
            "syntax_number": "#E8A05A",
            "syntax_keyword": "#E07B32",
            "syntax_comment": "#7E7466",
        },
        "light_colors": {
            "background": "#F4F1EA",
            "banner_border": "#8C7A5B",
            "banner_title": "#8A3B12",
            "banner_accent": "#B4541A",
            "banner_dim": "#6E675C",
            "banner_text": "#2B2A27",
            "ui_accent": "#B4541A",
            "ui_label": "#8A6A21",
            "ui_ok": "#2F6B33",
            "ui_error": "#A32218",
            "ui_warn": "#B07105",
            "ui_tool": "#8A3B12",
            "ui_thinking": "#7A6A46",
            "prompt": "#2B2A27",
            "input_rule": "#B39A72",
            "response_border": "#B4541A",
            "status_bar_bg": "#EDE8DE",
            "status_bar_text": "#2B2A27",
            "status_bar_strong": "#8A3B12",
            "status_bar_dim": "#8C8375",
            "status_bar_good": "#2F6B33",
            "status_bar_warn": "#B07105",
            "status_bar_bad": "#C0561A",
            "status_bar_critical": "#A32218",
            "session_label": "#8A6A21",
            "session_border": "#A89C88",
            "completion_menu_bg": "#EFEAE0",
            "completion_menu_current_bg": "#E0D6C4",
            "completion_menu_meta_bg": "#E9E3D7",
            "completion_menu_meta_current_bg": "#D6C9B2",
            "selection_bg": "#E4D9C6",
            "shell_dollar": "#B4541A",
            "voice_status_bg": "#EDE8DE",
            "diff_added": "#DEEBD8",
            "diff_removed": "#F2DCD8",
            "diff_added_word": "#2F6B33",
            "diff_removed_word": "#A32218",
            "syntax_string": "#8A6A21",
            "syntax_number": "#8A3B12",
            "syntax_keyword": "#8E2F14",
            "syntax_comment": "#8C8375",
        },
        "spinner": {
            "waiting_faces": ["[▖]", "[▘]", "[▝]", "[▗]"],
            "thinking_faces": ["[░]", "[▒]", "[▓]", "[█]", "[▓]", "[▒]"],
            "thinking_verbs": [
                "calibrating",
                "weighing out",
                "cross-checking",
                "collating",
                "indexing",
                "reading the dial",
                "tabulating",
                "running the assay",
                "checking the standard",
                "logging to the notebook",
                "measuring twice",
                "annealing",
                "developing the plate",
                "filing the record",
                "squaring the numbers",
                "reducing the sample",
            ],
            "wings": [
                ["╢", "╟"],
                ["◄", "►"],
                ["▐", "▌"],
                ["╡", "╞"],
            ],
        },
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.",
            "goodbye": "Bench secured. Logbook closed. ▮",
            "response_label": " ▮ CURIE ",
            "prompt_symbol": "▶",
            "help_header": "▮ COMMAND INDEX",
        },
        "tool_prefix": "│",
        "banner_logo": """[bold #E8A05A] ██████╗██╗   ██╗██████╗ ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #E8A05A]██╔════╝██║   ██║██╔══██╗██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#E07B32]██║     ██║   ██║██████╔╝██║█████╗  █████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   [/]
[#E07B32]██║     ██║   ██║██╔══██╗██║██╔══╝  ╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   [/]
[#D6B25E]╚██████╗╚██████╔╝██║  ██║██║███████╗      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   [/]
[#8A6A3B] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   [/]""",
        "banner_logo_medium": """[bold #E8A05A] ██████╗██╗   ██╗██████╗ ██╗███████╗[/]
[bold #E8A05A]██╔════╝██║   ██║██╔══██╗██║██╔════╝[/]
[#E07B32]██║     ██║   ██║██████╔╝██║█████╗  [/]
[#E07B32]██║     ██║   ██║██╔══██╗██║██╔══╝  [/]
[#D6B25E]╚██████╗╚██████╔╝██║  ██║██║███████╗[/]
[#8A6A3B] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝[/]""",
        "banner_logo_compact": """[bold #E8A05A]╒═══ C U R I E ═══╕[/]
[#8A6A3B]╘═ bench terminal ═╛[/]""",
        "banner_hero": """[#8A6A3B]╔════════════════════════════╗[/]
[#8A6A3B]║[/][bold #EFE7D8] CURIE · STRIP CHART  No.4  [/][#8A6A3B]║[/]
[#8A6A3B]╠════════════════════════════╣[/]
[#8A6A3B]║[/][#8A6A3B]100┤[/]                        [#8A6A3B]║[/]
[#8A6A3B]║[/][#8A6A3B]   ┤[/][#E07B32]         ▗▄▖            [/][#8A6A3B]║[/]
[#8A6A3B]║[/][#8A6A3B] 50┤[/][#E07B32]    ▗▄▟▀▘   ▝▀▙▄▖       [/][#8A6A3B]║[/]
[#8A6A3B]║[/][#8A6A3B]   ┤[/][#E07B32] ▄▟▀▘          ▝▀▙▄▄▄   [/][#8A6A3B]║[/]
[#8A6A3B]║[/][#8A6A3B]  0┼────────────────────────[/][#8A6A3B]║[/]
[#8A6A3B]║[/][#8A6A3B]   0    5   10   15   20min [/][#8A6A3B]║[/]
[#8A6A3B]╠════════════════════════════╣[/]
[#8A6A3B]║[/] [#6FBF73]▉[/][#EFE7D8] MAINS[/]  [#6FBF73]▉[/][#EFE7D8] BENCH[/]  [#E0A63A]░[/][#EFE7D8] LOG[/]   [#8A6A3B]║[/]
[#8A6A3B]╚════════════════════════════╝[/]""",
    },
    "curie-vga": {
        "name": "curie-vga",
        "description": "The IBM VGA sixteen — Turbo Vision blue on a dark screen",
        "colors": {
            "background": "#0000AA",
            "banner_border": "#55FFFF",
            "banner_title": "#FFFF55",
            "banner_accent": "#55FFFF",
            "banner_dim": "#AAAAAA",
            "banner_text": "#FFFFFF",
            "ui_accent": "#55FFFF",
            "ui_label": "#FFFF55",
            "ui_ok": "#55FF55",
            "ui_error": "#FF5555",
            "ui_warn": "#FFFF55",
            "ui_tool": "#55FFFF",
            "ui_thinking": "#FF55FF",
            "prompt": "#FFFFFF",
            "input_rule": "#55FFFF",
            "response_border": "#FFFF55",
            "status_bar_bg": "#000000",
            "status_bar_text": "#FFFFFF",
            "status_bar_strong": "#FFFF55",
            "status_bar_dim": "#AAAAAA",
            "status_bar_good": "#55FF55",
            "status_bar_warn": "#FFFF55",
            "status_bar_bad": "#FF5555",
            "status_bar_critical": "#FF55FF",
            "session_label": "#FFFF55",
            "session_border": "#AAAAAA",
            "completion_menu_bg": "#00AAAA",
            "completion_menu_current_bg": "#0000AA",
            "completion_menu_meta_bg": "#008888",
            "completion_menu_meta_current_bg": "#000088",
            "selection_bg": "#00AAAA",
            "shell_dollar": "#55FFFF",
            "voice_status_bg": "#555555",
            "diff_added": "#004400",
            "diff_removed": "#550000",
            "diff_added_word": "#55FF55",
            "diff_removed_word": "#FF5555",
            "syntax_string": "#FFFF55",
            "syntax_number": "#FF55FF",
            "syntax_keyword": "#55FFFF",
            "syntax_comment": "#AAAAAA",
        },
        "spinner": {
            "waiting_faces": ["[▖]", "[▘]", "[▝]", "[▗]"],
            "thinking_faces": ["[░]", "[▒]", "[▓]", "[█]", "[▓]", "[▒]"],
            "thinking_verbs": [
                "calibrating",
                "weighing out",
                "cross-checking",
                "collating",
                "indexing",
                "reading the dial",
                "tabulating",
                "running the assay",
                "checking the standard",
                "logging to the notebook",
                "measuring twice",
                "annealing",
                "developing the plate",
                "filing the record",
                "squaring the numbers",
                "reducing the sample",
            ],
            "wings": [
                ["╢", "╟"],
                ["◄", "►"],
                ["▐", "▌"],
                ["╡", "╞"],
            ],
        },
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.",
            "goodbye": "Bench secured. Logbook closed. ▮",
            "response_label": " ■ CURIE ",
            "prompt_symbol": "►",
            "help_header": "▮ COMMAND INDEX",
        },
        "tool_prefix": "║",
        "banner_logo": """[bold #0000AA] ██████╗██╗   ██╗██████╗ ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #0000AA]██╔════╝██║   ██║██╔══██╗██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#AA0000]██║     ██║   ██║██████╔╝██║█████╗  █████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   [/]
[#AA0000]██║     ██║   ██║██╔══██╗██║██╔══╝  ╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   [/]
[#AA5500]╚██████╗╚██████╔╝██║  ██║██║███████╗      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   [/]
[#555555] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   [/]""",
        "banner_logo_medium": """[bold #0000AA] ██████╗██╗   ██╗██████╗ ██╗███████╗[/]
[bold #0000AA]██╔════╝██║   ██║██╔══██╗██║██╔════╝[/]
[#AA0000]██║     ██║   ██║██████╔╝██║█████╗  [/]
[#AA0000]██║     ██║   ██║██╔══██╗██║██╔══╝  [/]
[#AA5500]╚██████╗╚██████╔╝██║  ██║██║███████╗[/]
[#555555] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝[/]""",
        "banner_logo_compact": """[bold #0000AA]╒═══ C U R I E ═══╕[/]
[#555555]╘═ bench terminal ═╛[/]""",
        "banner_hero": """[#555555]╔════════════════════════════╗[/]
[#555555]║[/][bold #000000] CURIE · STRIP CHART  No.4  [/][#555555]║[/]
[#555555]╠════════════════════════════╣[/]
[#555555]║[/][#555555]100┤[/]                        [#555555]║[/]
[#555555]║[/][#555555]   ┤[/][#AA0000]         ▗▄▖            [/][#555555]║[/]
[#555555]║[/][#555555] 50┤[/][#AA0000]    ▗▄▟▀▘   ▝▀▙▄▖       [/][#555555]║[/]
[#555555]║[/][#555555]   ┤[/][#AA0000] ▄▟▀▘          ▝▀▙▄▄▄   [/][#555555]║[/]
[#555555]║[/][#555555]  0┼────────────────────────[/][#555555]║[/]
[#555555]║[/][#555555]   0    5   10   15   20min [/][#555555]║[/]
[#555555]╠════════════════════════════╣[/]
[#555555]║[/] [#00AA00]▉[/][#000000] MAINS[/]  [#00AA00]▉[/][#000000] BENCH[/]  [#AA5500]░[/][#000000] LOG[/]   [#555555]║[/]
[#555555]╚════════════════════════════╝[/]""",
    },
    "curie-amber": {
        "name": "curie-amber",
        "description": "Amber phosphor — one hue, hierarchy by brightness alone",
        "colors": {
            "background": "#F3EDE1",
            "banner_border": "#9A6B12",
            "banner_title": "#7A4A05",
            "banner_accent": "#A8600A",
            "banner_dim": "#8C7A5C",
            "banner_text": "#33291A",
            "ui_accent": "#A8600A",
            "ui_label": "#8A6410",
            "ui_ok": "#5E6B12",
            "ui_error": "#A33A05",
            "ui_warn": "#B07105",
            "ui_tool": "#7A4A05",
            "ui_thinking": "#9A8352",
            "prompt": "#33291A",
            "input_rule": "#B79A62",
            "response_border": "#A8600A",
            "status_bar_bg": "#EBE3D2",
            "status_bar_text": "#33291A",
            "status_bar_strong": "#7A4A05",
            "status_bar_dim": "#7E7259",
            "status_bar_good": "#5E6B12",
            "status_bar_warn": "#B07105",
            "status_bar_bad": "#A8600A",
            "status_bar_critical": "#A33A05",
            "session_label": "#8A6410",
            "session_border": "#AD9E7E",
            "completion_menu_bg": "#EFE7D6",
            "completion_menu_current_bg": "#DFCFA8",
            "completion_menu_meta_bg": "#E9E0CC",
            "completion_menu_meta_current_bg": "#D5C293",
            "selection_bg": "#E2D3AE",
            "shell_dollar": "#A8600A",
            "diff_added": "#E6E4C8",
            "diff_removed": "#F0DDC6",
            "diff_added_word": "#5E6B12",
            "diff_removed_word": "#A33A05",
            "syntax_string": "#8A6410",
            "syntax_number": "#A8600A",
            "syntax_keyword": "#7A4A05",
            "syntax_comment": "#93866B",
            "voice_status_bg": "#EBE3D2",
        },
        "dark_colors": {
            "background": "#140F05",
            "banner_border": "#8A5A00",
            "banner_title": "#FFB000",
            "banner_accent": "#FF9E1B",
            "banner_dim": "#A87A28",
            "banner_text": "#FFCC66",
            "ui_accent": "#FF9E1B",
            "ui_label": "#E0A63A",
            "ui_ok": "#D2C24A",
            "ui_error": "#FF7043",
            "ui_warn": "#FFB000",
            "ui_tool": "#FFB000",
            "ui_thinking": "#BD9440",
            "prompt": "#FFCC66",
            "input_rule": "#6E4E12",
            "response_border": "#FFB000",
            "status_bar_bg": "#1E1608",
            "status_bar_text": "#FFCC66",
            "status_bar_strong": "#FFB000",
            "status_bar_dim": "#8A6C30",
            "status_bar_good": "#D2C24A",
            "status_bar_warn": "#FFB000",
            "status_bar_bad": "#FF9E1B",
            "status_bar_critical": "#FF7043",
            "session_label": "#E0A63A",
            "session_border": "#5A4418",
            "completion_menu_bg": "#1E1608",
            "completion_menu_current_bg": "#3E2E0C",
            "completion_menu_meta_bg": "#191204",
            "completion_menu_meta_current_bg": "#33260A",
            "selection_bg": "#4A370E",
            "shell_dollar": "#FF9E1B",
            "diff_added": "#232006",
            "diff_removed": "#301603",
            "diff_added_word": "#D2C24A",
            "diff_removed_word": "#FF7043",
            "syntax_string": "#E0A63A",
            "syntax_number": "#FFB000",
            "syntax_keyword": "#FF9E1B",
            "syntax_comment": "#8A6C30",
            "voice_status_bg": "#1E1608",
        },
        "spinner": {
            "waiting_faces": ["[▖]", "[▘]", "[▝]", "[▗]"],
            "thinking_faces": ["[░]", "[▒]", "[▓]", "[█]", "[▓]", "[▒]"],
            "thinking_verbs": [
                "calibrating",
                "weighing out",
                "cross-checking",
                "collating",
                "indexing",
                "reading the dial",
                "tabulating",
                "running the assay",
                "checking the standard",
                "logging to the notebook",
                "measuring twice",
                "annealing",
                "developing the plate",
                "filing the record",
                "squaring the numbers",
                "reducing the sample",
            ],
            "wings": [
                ["╢", "╟"],
                ["◄", "►"],
                ["▐", "▌"],
                ["╡", "╞"],
            ],
        },
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.",
            "goodbye": "Bench secured. Logbook closed. ▮",
            "response_label": " ▄ CURIE ",
            "prompt_symbol": "›",
            "help_header": "▮ COMMAND INDEX",
        },
        "tool_prefix": "▏",
        "banner_logo": """[bold #7A4A05] ██████╗██╗   ██╗██████╗ ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #7A4A05]██╔════╝██║   ██║██╔══██╗██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#A8600A]██║     ██║   ██║██████╔╝██║█████╗  █████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   [/]
[#A8600A]██║     ██║   ██║██╔══██╗██║██╔══╝  ╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   [/]
[#9A6B12]╚██████╗╚██████╔╝██║  ██║██║███████╗      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   [/]
[#8C7A5C] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   [/]""",
        "banner_logo_medium": """[bold #7A4A05] ██████╗██╗   ██╗██████╗ ██╗███████╗[/]
[bold #7A4A05]██╔════╝██║   ██║██╔══██╗██║██╔════╝[/]
[#A8600A]██║     ██║   ██║██████╔╝██║█████╗  [/]
[#A8600A]██║     ██║   ██║██╔══██╗██║██╔══╝  [/]
[#9A6B12]╚██████╗╚██████╔╝██║  ██║██║███████╗[/]
[#8C7A5C] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝[/]""",
        "banner_logo_compact": """[bold #7A4A05]╒═══ C U R I E ═══╕[/]
[#9A6B12]╘═ bench terminal ═╛[/]""",
        "banner_hero": """[#9A6B12]╔════════════════════════════╗[/]
[#9A6B12]║[/][bold #33291A] CURIE · STRIP CHART  No.4  [/][#9A6B12]║[/]
[#9A6B12]╠════════════════════════════╣[/]
[#9A6B12]║[/][#9A6B12]100┤[/]                        [#9A6B12]║[/]
[#9A6B12]║[/][#9A6B12]   ┤[/][#A8600A]         ▗▄▖            [/][#9A6B12]║[/]
[#9A6B12]║[/][#9A6B12] 50┤[/][#A8600A]    ▗▄▟▀▘   ▝▀▙▄▖       [/][#9A6B12]║[/]
[#9A6B12]║[/][#9A6B12]   ┤[/][#A8600A] ▄▟▀▘          ▝▀▙▄▄▄   [/][#9A6B12]║[/]
[#9A6B12]║[/][#9A6B12]  0┼────────────────────────[/][#9A6B12]║[/]
[#9A6B12]║[/][#9A6B12]   0    5   10   15   20min [/][#9A6B12]║[/]
[#9A6B12]╠════════════════════════════╣[/]
[#9A6B12]║[/] [#5E6B12]▉[/][#33291A] MAINS[/]  [#5E6B12]▉[/][#33291A] BENCH[/]  [#B07105]░[/][#33291A] LOG[/]   [#9A6B12]║[/]
[#9A6B12]╚════════════════════════════╝[/]""",
    },
    "curie-blueprint": {
        "name": "curie-blueprint",
        "description": "Cyanotype — drafting ink on paper, contact-printed",
        "colors": {
            "background": "#F0F2F5",
            "banner_border": "#4A6E8F",
            "banner_title": "#123A63",
            "banner_accent": "#1B5E8C",
            "banner_dim": "#5F7182",
            "banner_text": "#1B2733",
            "ui_accent": "#1B5E8C",
            "ui_label": "#37678C",
            "ui_ok": "#2C6B4F",
            "ui_error": "#9C2A20",
            "ui_warn": "#9A6410",
            "ui_tool": "#123A63",
            "ui_thinking": "#5A7590",
            "prompt": "#1B2733",
            "input_rule": "#93A8BD",
            "response_border": "#1B5E8C",
            "status_bar_bg": "#E4E9EF",
            "status_bar_text": "#1B2733",
            "status_bar_strong": "#123A63",
            "status_bar_dim": "#69788A",
            "status_bar_good": "#2C6B4F",
            "status_bar_warn": "#9A6410",
            "status_bar_bad": "#B4541A",
            "status_bar_critical": "#9C2A20",
            "session_label": "#37678C",
            "session_border": "#9AA9B8",
            "completion_menu_bg": "#E8EDF3",
            "completion_menu_current_bg": "#C6D6E6",
            "completion_menu_meta_bg": "#E1E7EE",
            "completion_menu_meta_current_bg": "#B6CADE",
            "selection_bg": "#CBDAE9",
            "shell_dollar": "#1B5E8C",
            "diff_added": "#D8E8DE",
            "diff_removed": "#EEDAD7",
            "diff_added_word": "#2C6B4F",
            "diff_removed_word": "#9C2A20",
            "syntax_string": "#37678C",
            "syntax_number": "#123A63",
            "syntax_keyword": "#1B5E8C",
            "syntax_comment": "#7C8B9A",
            "voice_status_bg": "#E4E9EF",
        },
        "dark_colors": {
            "background": "#0B2447",
            "banner_border": "#4E86BE",
            "banner_title": "#E8F1FA",
            "banner_accent": "#9CC8ED",
            "banner_dim": "#6E93B8",
            "banner_text": "#DCE9F5",
            "ui_accent": "#9CC8ED",
            "ui_label": "#B9D6EE",
            "ui_ok": "#6FC79B",
            "ui_error": "#E88070",
            "ui_warn": "#E8BE6A",
            "ui_tool": "#E8F1FA",
            "ui_thinking": "#87A9C9",
            "prompt": "#DCE9F5",
            "input_rule": "#3C6A96",
            "response_border": "#9CC8ED",
            "status_bar_bg": "#123157",
            "status_bar_text": "#DCE9F5",
            "status_bar_strong": "#E8F1FA",
            "status_bar_dim": "#5B7FA3",
            "status_bar_good": "#6FC79B",
            "status_bar_warn": "#E8BE6A",
            "status_bar_bad": "#E89A5A",
            "status_bar_critical": "#E88070",
            "session_label": "#B9D6EE",
            "session_border": "#365A80",
            "completion_menu_bg": "#123157",
            "completion_menu_current_bg": "#1E4C7E",
            "completion_menu_meta_bg": "#0F2A4C",
            "completion_menu_meta_current_bg": "#1A4370",
            "selection_bg": "#22568C",
            "shell_dollar": "#9CC8ED",
            "diff_added": "#123C2C",
            "diff_removed": "#3E1E1A",
            "diff_added_word": "#6FC79B",
            "diff_removed_word": "#E88070",
            "syntax_string": "#B9D6EE",
            "syntax_number": "#E8F1FA",
            "syntax_keyword": "#9CC8ED",
            "syntax_comment": "#5B7FA3",
            "voice_status_bg": "#123157",
        },
        "spinner": {
            "waiting_faces": ["[▖]", "[▘]", "[▝]", "[▗]"],
            "thinking_faces": ["[░]", "[▒]", "[▓]", "[█]", "[▓]", "[▒]"],
            "thinking_verbs": [
                "calibrating",
                "weighing out",
                "cross-checking",
                "collating",
                "indexing",
                "reading the dial",
                "tabulating",
                "running the assay",
                "checking the standard",
                "logging to the notebook",
                "measuring twice",
                "annealing",
                "developing the plate",
                "filing the record",
                "squaring the numbers",
                "reducing the sample",
            ],
            "wings": [
                ["╢", "╟"],
                ["◄", "►"],
                ["▐", "▌"],
                ["╡", "╞"],
            ],
        },
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "CURIE AGENT — bench terminal ready. Type a request, or /help for the command index.",
            "goodbye": "Bench secured. Logbook closed. ▮",
            "response_label": " ◈ CURIE ",
            "prompt_symbol": "→",
            "help_header": "▮ COMMAND INDEX",
        },
        "tool_prefix": "┆",
        "banner_logo": """[bold #123A63] ██████╗██╗   ██╗██████╗ ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #123A63]██╔════╝██║   ██║██╔══██╗██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#1B5E8C]██║     ██║   ██║██████╔╝██║█████╗  █████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   [/]
[#1B5E8C]██║     ██║   ██║██╔══██╗██║██╔══╝  ╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   [/]
[#37678C]╚██████╗╚██████╔╝██║  ██║██║███████╗      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   [/]
[#4A6E8F] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   [/]""",
        "banner_logo_medium": """[bold #123A63] ██████╗██╗   ██╗██████╗ ██╗███████╗[/]
[bold #123A63]██╔════╝██║   ██║██╔══██╗██║██╔════╝[/]
[#1B5E8C]██║     ██║   ██║██████╔╝██║█████╗  [/]
[#1B5E8C]██║     ██║   ██║██╔══██╗██║██╔══╝  [/]
[#37678C]╚██████╗╚██████╔╝██║  ██║██║███████╗[/]
[#4A6E8F] ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝[/]""",
        "banner_logo_compact": """[bold #123A63]╒═══ C U R I E ═══╕[/]
[#4A6E8F]╘═ bench terminal ═╛[/]""",
        "banner_hero": """[#4A6E8F]╔════════════════════════════╗[/]
[#4A6E8F]║[/][bold #1B2733] CURIE · STRIP CHART  No.4  [/][#4A6E8F]║[/]
[#4A6E8F]╠════════════════════════════╣[/]
[#4A6E8F]║[/][#4A6E8F]100┤[/]                        [#4A6E8F]║[/]
[#4A6E8F]║[/][#4A6E8F]   ┤[/][#1B5E8C]         ▗▄▖            [/][#4A6E8F]║[/]
[#4A6E8F]║[/][#4A6E8F] 50┤[/][#1B5E8C]    ▗▄▟▀▘   ▝▀▙▄▖       [/][#4A6E8F]║[/]
[#4A6E8F]║[/][#4A6E8F]   ┤[/][#1B5E8C] ▄▟▀▘          ▝▀▙▄▄▄   [/][#4A6E8F]║[/]
[#4A6E8F]║[/][#4A6E8F]  0┼────────────────────────[/][#4A6E8F]║[/]
[#4A6E8F]║[/][#4A6E8F]   0    5   10   15   20min [/][#4A6E8F]║[/]
[#4A6E8F]╠════════════════════════════╣[/]
[#4A6E8F]║[/] [#2C6B4F]▉[/][#1B2733] MAINS[/]  [#2C6B4F]▉[/][#1B2733] BENCH[/]  [#9A6410]░[/][#1B2733] LOG[/]   [#4A6E8F]║[/]
[#4A6E8F]╚════════════════════════════╝[/]""",
    },
    "mono": {
        "name": "mono",
        "description": "Monochrome — clean grayscale",
        "colors": {
            "banner_border": "#5E5E5E",
            "banner_title": "#e6edf3",
            "banner_accent": "#aaaaaa",
            "banner_dim": "#606060",
            "banner_text": "#FFFFFF",
            "ui_accent": "#aaaaaa",
            "ui_label": "#888888",
            "ui_ok": "#888888",
            "ui_error": "#cccccc",
            "ui_warn": "#999999",
            "prompt": "#FFFFFF",
            "input_rule": "#606060",
            "response_border": "#aaaaaa",
            "status_bar_bg": "#1F1F1F",
            "status_bar_text": "#C9D1D9",
            "status_bar_strong": "#E6EDF3",
            "status_bar_dim": "#777777",
            "status_bar_good": "#B5B5B5",
            "status_bar_warn": "#AAAAAA",
            "status_bar_bad": "#D0D0D0",
            "status_bar_critical": "#F0F0F0",
            "session_label": "#888888",
            "session_border": "#5E5E5E",
            "completion_menu_bg": "#1F1F1F",
            "completion_menu_current_bg": "#464646",
            "selection_bg": "#505050",
            "shell_dollar": "#aaaaaa",
            "voice_status_bg": "#1F1F1F",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "Welcome to Curie Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ▮",
            "response_label": " ▮ Curie ",
            "prompt_symbol": "❯",
            "help_header": "[?] Available Commands",
        },
        "tool_prefix": "┊",
    },
    "slate": {
        "name": "slate",
        "description": "Cool blue — developer-focused",
        "colors": {
            "banner_border": "#4169e1",
            "banner_title": "#7eb8f6",
            "banner_accent": "#8EA8FF",
            "banner_dim": "#545E6B",
            "banner_text": "#FFFFFF",
            "ui_accent": "#7eb8f6",
            "ui_label": "#8EA8FF",
            "ui_ok": "#63D0A6",
            "ui_error": "#F7A072",
            "ui_warn": "#e6a855",
            "prompt": "#FFFFFF",
            "input_rule": "#4169e1",
            "response_border": "#7eb8f6",
            "status_bar_bg": "#151C2F",
            "status_bar_text": "#C9D1D9",
            "status_bar_strong": "#7EB8F6",
            "status_bar_dim": "#5D6672",
            "status_bar_good": "#63D0A6",
            "status_bar_warn": "#E6A855",
            "status_bar_bad": "#F7A072",
            "status_bar_critical": "#FF7A7A",
            "session_label": "#7eb8f6",
            "session_border": "#545E6B",
            "completion_menu_bg": "#151C2F",
            "completion_menu_current_bg": "#324867",
            "selection_bg": "#3A5375",
            "shell_dollar": "#7eb8f6",
            "voice_status_bg": "#151C2F",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "Welcome to Curie Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ▮",
            "response_label": " ▮ Curie ",
            "prompt_symbol": "❯",
            "help_header": "(^_^)? Available Commands",
        },
        "tool_prefix": "┊",
    },
    "daylight": {
        "name": "daylight",
        "description": "Light theme for bright terminals with dark text and cool blue accents",
        "colors": {
            "banner_border": "#2563EB",
            "banner_title": "#0F172A",
            "banner_accent": "#1D4ED8",
            "banner_dim": "#475569",
            "banner_text": "#111827",
            "ui_accent": "#2563EB",
            "ui_label": "#0F766E",
            "ui_ok": "#15803D",
            "ui_error": "#B91C1C",
            "ui_warn": "#B45309",
            "prompt": "#111827",
            "input_rule": "#6E94BE",
            "response_border": "#2563EB",
            "status_bar_bg": "#E5EDF8",
            "status_bar_text": "#111827",
            "status_bar_strong": "#2563EB",
            "status_bar_dim": "#838890",
            "status_bar_good": "#15803D",
            "status_bar_warn": "#B45309",
            "status_bar_bad": "#B45309",
            "status_bar_critical": "#B91C1C",
            "session_label": "#1D4ED8",
            "session_border": "#64748B",
            "completion_menu_bg": "#F8FAFC",
            "completion_menu_current_bg": "#DBEAFE",
            "completion_menu_meta_bg": "#EEF2FF",
            "completion_menu_meta_current_bg": "#BFDBFE",
            "selection_bg": "#D3E0FB",
            "shell_dollar": "#2563EB",
            "voice_status_bg": "#E5EDF8",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "Welcome to Curie Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ▮",
            "response_label": " ▮ Curie ",
            "prompt_symbol": "❯",
            "help_header": "[?] Available Commands",
        },
        "tool_prefix": "│",
    },
    "warm-lightmode": {
        "name": "warm-lightmode",
        "description": "Warm light mode — dark brown/gold text for light terminal backgrounds",
        "colors": {
            "banner_border": "#8B6914",
            "banner_title": "#5C3D11",
            "banner_accent": "#8B4513",
            "banner_dim": "#8B7355",
            "banner_text": "#2C1810",
            "ui_accent": "#8B4513",
            "ui_label": "#5C3D11",
            "ui_ok": "#2E7D32",
            "ui_error": "#C62828",
            "ui_warn": "#E65100",
            "prompt": "#2C1810",
            "input_rule": "#8B6914",
            "response_border": "#8B6914",
            "status_bar_bg": "#F5F0E8",
            "status_bar_text": "#2C1810",
            "status_bar_strong": "#8B4513",
            "status_bar_dim": "#8A8F98",
            "status_bar_good": "#2E7D32",
            "status_bar_warn": "#E65100",
            "status_bar_bad": "#DA4D00",
            "status_bar_critical": "#C62828",
            "session_label": "#5C3D11",
            "session_border": "#A0845C",
            "completion_menu_bg": "#F5EFE0",
            "completion_menu_current_bg": "#E8DCC8",
            "completion_menu_meta_bg": "#F0E8D8",
            "completion_menu_meta_current_bg": "#DFCFB0",
            "selection_bg": "#E8DAD0",
            "shell_dollar": "#8B4513",
            "voice_status_bg": "#F5F0E8",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Curie Agent",
            "welcome": "Welcome to Curie Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! \u2695",
            "response_label": " \u2695 Curie ",
            "prompt_symbol": "\u276f",
            "help_header": "(^_^)? Available Commands",
        },
        "tool_prefix": "\u250a",
    },
}


# =============================================================================
# Skin loading and management
# =============================================================================

_active_skin: Optional[SkinConfig] = None
_active_skin_name: str = "default"


def _skins_dir() -> Path:
    """User skins directory."""
    return get_curie_home() / "skins"


def _load_skin_from_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """Load a skin definition from a YAML file."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "name" in data:
            return data
    except Exception as e:
        logger.debug("Failed to load skin from %s: %s", path, e)
    return None


def _mapping_or_empty(value: Any, *, section: str, skin_name: str) -> Dict[str, Any]:
    """Return a mapping value or an empty dict when the section type is invalid."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    logger.warning(
        "Skin '%s' has invalid '%s' section type (%s); ignoring section",
        skin_name,
        section,
        type(value).__name__,
    )
    return {}


def _build_skin_config(data: Dict[str, Any]) -> SkinConfig:
    """Build a SkinConfig from a raw dict (built-in or loaded from YAML)."""
    # Start with default values as base for missing keys
    default = _BUILTIN_SKINS["default"]
    skin_name = str(data.get("name", "unknown"))
    color_overrides = _mapping_or_empty(data.get("colors"), section="colors", skin_name=skin_name)
    spinner_overrides = _mapping_or_empty(data.get("spinner"), section="spinner", skin_name=skin_name)
    branding_overrides = _mapping_or_empty(data.get("branding"), section="branding", skin_name=skin_name)
    emoji_overrides = _mapping_or_empty(data.get("tool_emojis"), section="tool_emojis", skin_name=skin_name)

    colors = dict(default.get("colors", {}))
    colors.update(color_overrides)
    spinner = dict(default.get("spinner", {}))
    spinner.update(spinner_overrides)
    branding = dict(default.get("branding", {}))
    branding.update(branding_overrides)

    # Paired palettes are NOT merged over the default skin's blocks: an empty
    # block means "this skin has no hand-tuned variant for that polarity", and
    # consumers (the TUI) fall back to `colors` + automatic adaptation. Merging
    # the default's gold light palette under a crimson skin would be worse
    # than adapting the crimson.
    light_colors = _mapping_or_empty(data.get("light_colors"), section="light_colors", skin_name=skin_name)
    dark_colors = _mapping_or_empty(data.get("dark_colors"), section="dark_colors", skin_name=skin_name)

    return SkinConfig(
        name=skin_name,
        description=data.get("description", ""),
        colors=colors,
        light_colors=light_colors,
        dark_colors=dark_colors,
        spinner=spinner,
        branding=branding,
        tool_prefix=data.get("tool_prefix", default.get("tool_prefix", "┊")),
        tool_emojis=emoji_overrides,
        banner_logo=data.get("banner_logo", ""),
        banner_logo_medium=data.get("banner_logo_medium", ""),
        banner_logo_compact=data.get("banner_logo_compact", ""),
        banner_hero=data.get("banner_hero", ""),
    )


def list_skins() -> List[Dict[str, str]]:
    """List all available skins (built-in + user-installed).

    Returns list of {"name": ..., "description": ..., "source": "builtin"|"user"}.
    """
    result = []
    for name, data in _BUILTIN_SKINS.items():
        result.append({
            "name": name,
            "description": data.get("description", ""),
            "source": "builtin",
        })

    skins_path = _skins_dir()
    if skins_path.is_dir():
        for f in sorted(skins_path.glob("*.yaml")):
            data = _load_skin_from_yaml(f)
            if data:
                skin_name = data.get("name", f.stem)
                # Skip if it shadows a built-in
                if any(s["name"] == skin_name for s in result):
                    continue
                result.append({
                    "name": skin_name,
                    "description": data.get("description", ""),
                    "source": "user",
                })

    return result


def load_skin(name: str) -> SkinConfig:
    """Load a skin by name. Checks user skins first, then built-in."""
    # Check user skins directory
    skins_path = _skins_dir()
    user_file = skins_path / f"{name}.yaml"
    if user_file.is_file():
        data = _load_skin_from_yaml(user_file)
        if data:
            return _build_skin_config(data)

    # Check built-in skins
    if name in _BUILTIN_SKINS:
        return _build_skin_config(_BUILTIN_SKINS[name])

    # Fallback to default
    logger.warning("Skin '%s' not found, using default", name)
    return _build_skin_config(_BUILTIN_SKINS["default"])


def get_active_skin() -> SkinConfig:
    """Get the currently active skin config (cached)."""
    global _active_skin
    if _active_skin is None:
        _active_skin = load_skin(_active_skin_name)
    return _active_skin


def set_active_skin(name: str) -> SkinConfig:
    """Switch the active skin. Returns the new SkinConfig."""
    global _active_skin, _active_skin_name
    _active_skin_name = name
    _active_skin = load_skin(name)
    return _active_skin


def get_active_skin_name() -> str:
    """Get the name of the currently active skin."""
    return _active_skin_name


def init_skin_from_config(config: dict) -> None:
    """Initialize the active skin from CLI config at startup.

    Call this once during CLI init with the loaded config dict.
    """
    display = config.get("display") or {}
    if not isinstance(display, dict):
        display = {}
    skin_name = display.get("skin", "default")
    if isinstance(skin_name, str) and skin_name.strip():
        set_active_skin(skin_name.strip())
    else:
        set_active_skin("default")


# =============================================================================
# Convenience helpers for CLI modules
# =============================================================================


def get_active_prompt_symbol(fallback: str = "❯") -> str:
    """Return the interactive prompt symbol with a single trailing space.

    Skins store ``prompt_symbol`` as a bare token (no spaces). The trailing
    space is appended here so callers can drop it straight into a rendered
    prompt without hand-rolling whitespace.
    """
    try:
        raw = get_active_skin().get_branding("prompt_symbol", fallback)
    except Exception:
        raw = fallback

    cleaned = (raw or fallback).strip()

    return f"{cleaned or fallback.strip()} "



def get_active_help_header(fallback: str = "(^_^)? Available Commands") -> str:
    """Get the /help header from the active skin."""
    try:
        return get_active_skin().get_branding("help_header", fallback)
    except Exception:
        return fallback



def get_active_goodbye(fallback: str = "Goodbye! ▮") -> str:
    """Get the goodbye line from the active skin."""
    try:
        return get_active_skin().get_branding("goodbye", fallback)
    except Exception:
        return fallback



def get_prompt_toolkit_style_overrides() -> Dict[str, str]:
    """Return prompt_toolkit style overrides derived from the active skin.

    These are layered on top of the CLI's base TUI style so /skin can refresh
    the live prompt_toolkit UI immediately without rebuilding the app.
    """
    try:
        skin = get_active_skin()
    except Exception:
        return {}

    # Input/prompt: leave unset by default so the typed text inherits
    # the terminal's foreground color (readable in both light and dark
    # color schemes).  Skins can opt into a colored prompt by setting
    # `prompt` explicitly in their YAML.
    prompt = skin.get_color("prompt", "")
    input_rule = skin.get_color("input_rule", "#CD7F32")
    title = skin.get_color("banner_title", "#FFD700")
    text = skin.get_color("banner_text", "#FFF8DC")
    dim = skin.get_color("banner_dim", "#555555")
    label = skin.get_color("ui_label", title)
    warn = skin.get_color("ui_warn", "#FF8C00")
    error = skin.get_color("ui_error", "#FF6B6B")
    status_bg = skin.get_color("status_bar_bg", "#1a1a2e")
    status_text = skin.get_color("status_bar_text", text)
    status_strong = skin.get_color("status_bar_strong", title)
    status_dim = skin.get_color("status_bar_dim", dim)
    status_good = skin.get_color("status_bar_good", skin.get_color("ui_ok", "#8FBC8F"))
    status_warn = skin.get_color("status_bar_warn", warn)
    status_bad = skin.get_color("status_bar_bad", skin.get_color("banner_accent", warn))
    status_critical = skin.get_color("status_bar_critical", error)
    voice_bg = skin.get_color("voice_status_bg", status_bg)
    menu_bg = skin.get_color("completion_menu_bg", "#1a1a2e")
    menu_current_bg = skin.get_color("completion_menu_current_bg", "#333355")
    menu_meta_bg = skin.get_color("completion_menu_meta_bg", menu_bg)
    menu_meta_current_bg = skin.get_color("completion_menu_meta_current_bg", menu_current_bg)

    return {
        # Typed input always uses terminal default fg/bg so it's
        # readable in both light and dark Terminal.app modes.  The
        # skin's `prompt` color (if any) only styles the prompt symbol,
        # NOT the user's typed text.
        "input-area": "",
        "placeholder": f"{dim} italic",
        "prompt": prompt,
        "prompt-working": f"{dim} italic",
        "hint": f"{dim} italic",
        "status-bar": f"bg:{status_bg} {status_text}",
        "status-bar-strong": f"bg:{status_bg} {status_strong} bold",
        "status-bar-dim": f"bg:{status_bg} {status_dim}",
        "status-bar-good": f"bg:{status_bg} {status_good} bold",
        "status-bar-warn": f"bg:{status_bg} {status_warn} bold",
        "status-bar-bad": f"bg:{status_bg} {status_bad} bold",
        "status-bar-critical": f"bg:{status_bg} {status_critical} bold",
        "input-rule": input_rule,
        "image-badge": f"{label} bold",
        "completion-menu": f"bg:{menu_bg} {text}",
        "completion-menu.completion": f"bg:{menu_bg} {text}",
        "completion-menu.completion.current": f"bg:{menu_current_bg} {title}",
        "completion-menu.meta.completion": f"bg:{menu_meta_bg} {dim}",
        "completion-menu.meta.completion.current": f"bg:{menu_meta_current_bg} {label}",
        "clarify-border": input_rule,
        "clarify-title": f"{title} bold",
        "clarify-question": f"{text} bold",
        "clarify-choice": dim,
        "clarify-selected": f"{title} bold",
        "clarify-active-other": f"{title} italic",
        "clarify-countdown": input_rule,
        "sudo-prompt": f"{error} bold",
        "sudo-border": input_rule,
        "sudo-title": f"{error} bold",
        "sudo-text": text,
        "approval-border": input_rule,
        "approval-title": f"{warn} bold",
        "approval-desc": f"{text} bold",
        "approval-cmd": f"{dim} italic",
        "approval-choice": dim,
        "approval-selected": f"{title} bold",
        "voice-status": f"bg:{voice_bg} {label}",
        "voice-status-recording": f"bg:{voice_bg} {error} bold",
    }
