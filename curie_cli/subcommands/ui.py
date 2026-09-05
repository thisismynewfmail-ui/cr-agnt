"""``curie ui`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_ui_parser(subparsers, *, cmd_ui: Callable) -> None:
    """Attach the mouse-driven bench console subcommand."""
    ui_parser = subparsers.add_parser(
        "ui",
        help="Open the mouse-driven bench console",
        description=(
            "Open the bench console: a full-screen, pointer-and-keyboard "
            "interface for running turns, reading the logbook, and checking "
            "what this install is wired up to. Works in any terminal that "
            "reports mouse events, including Windows Terminal and cmd.exe.\n"
            "\n"
            "Two skins: the instrument panel it opens in, and a DOS phosphor "
            "terminal. Switch on the PANEL pane (F6), under DISPLAY, or with "
            "`curie config set ui.skin_mode dos`."
        ),
    )
    ui_parser.add_argument(
        "--polarity",
        choices=("auto", "light", "dark"),
        default="auto",
        help=(
            "Which half of the active skin's palette to use. 'auto' reads "
            "the terminal's own hints and falls back to light."
        ),
    )
    ui_parser.set_defaults(func=cmd_ui)
