"""``curie ui`` — the mouse-driven bench console.

A full-screen, pointer-and-keyboard console for Curie, built on Textual so it
gets three things a plain terminal program cannot: mouse events, a layout that
reflows with the window, and a frame clock for the panel's live instruments.

The look is a mid-century instrument panel: enamel ground, stencilled
lettering, signal-coloured lamps, box-drawn chrome. Every colour comes from
the active skin (see :mod:`curie_cli.skin_engine`), so switching skins
re-themes this console along with the CLI and the TUI.

It has a second skin of its own. :mod:`curie_cli.bench_ui.dos` re-draws the
console as an amber phosphor terminal — one hue on dark glass, CP437 frames,
inverse-video bands, a numbered menu and Norton's function-key bar. It is a
*mode*, chosen on the PANEL pane and remembered in ``config.yaml``, and it
changes nothing about what the console does: same keys, same panes, same
instruments reading the same numbers.

Entry point: :func:`curie_cli.bench_ui.app.run`.
"""

from __future__ import annotations

__all__ = ["run", "MISSING_DEPENDENCY_HINT"]

MISSING_DEPENDENCY_HINT = (
    "`curie ui` needs the `ui` extra, which supplies Textual (the mouse and\n"
    "layout engine this console is built on).\n"
    "\n"
    "  Install it with:  curie update --ensure ui\n"
    "  Or directly:      pip install 'curie-agent[ui]'\n"
    "\n"
    "Everything else keeps working without it: `curie chat` for the classic\n"
    "prompt, `curie --tui` for the keyboard-driven interface."
)


def run(**kwargs) -> int:
    """Launch the console. Imports Textual lazily so `curie` starts fast."""
    from curie_cli.bench_ui.app import run as _run

    return _run(**kwargs)
