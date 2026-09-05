"""The console's own preferences, read from and written to ``config.yaml``.

What the bench console remembers between runs: which display mode it opens in
and, for the DOS one, the tube it draws with; which indicator set is on; and
whether it listens and speaks. They live in the same ``config.yaml`` as
everything else rather than in a private file, because a second settings store
is a second thing to find, back up and migrate — and because
``curie config set ui.indicators scope`` should work without this module being
involved at all.

Every read is total: a missing file, an unparseable one, or a value of the
wrong shape resolves to the documented default rather than raising. A console
that will not start because a preference is malformed is worse than one that
starts with the default set.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from curie_cli.bench_ui import dos
from curie_cli.bench_ui.indicators import DEFAULT_KIT, kit_names

#: Where each preference lives. Dotted paths, so they can be set from the CLI.
KEY_INDICATORS = "ui.indicators"
KEY_VOICE_INPUT = "ui.voice_input"
KEY_SPEAK_REPLIES = "ui.speak_replies"

#: Which of the console's two skins it opens in — the bench panel, or the DOS
#: phosphor terminal. Deliberately *not* ``display.skin``: that key names the
#: colour scheme shared with the CLI and the TUI, and this one names which
#: interface the console draws. Setting one must never silently change the
#: other, which is what a single key would have done.
KEY_SKIN_MODE = "ui.skin_mode"

#: The DOS mode's own settings. All under one section, so a reader who does
#: not use the mode never has to scroll past them and ``curie config`` can
#: show them as a block.
KEY_DOS_PHOSPHOR = "ui.dos.phosphor"
KEY_DOS_GLOW = "ui.dos.glow"
KEY_DOS_SCANLINES = "ui.dos.scanlines"
KEY_DOS_BLOCK_CURSOR = "ui.dos.block_cursor"

#: The skin, which the console shares with the CLI and the TUI rather than
#: keeping a second copy of. ``curie skin <name>`` writes the same key.
KEY_SKIN = "display.skin"

#: TTS keys the console shares with the rest of Curie rather than duplicating.
#: Picking a voice here is the same act as picking one in ``config.yaml``.
KEY_TTS_PROVIDER = "tts.provider"
KEY_PIPER_VOICE = "tts.piper.voice"
KEY_PIPER_VOICES_DIR = "tts.piper.voices_dir"

#: Piper writes a model and its metadata side by side; a voice is only usable
#: when both are there, so both are what the console looks for.
VOICE_SUFFIX = ".onnx"
VOICE_METADATA_SUFFIX = ".onnx.json"


@dataclass(frozen=True)
class BenchSettings:
    """Everything the console reads at start-up, already made safe."""

    indicators: str = DEFAULT_KIT
    voice_input: bool = False
    speak_replies: bool = False
    tts_provider: str = ""
    piper_voice: str = ""
    piper_voices_dir: str = ""
    skin: str = ""
    skin_mode: str = dos.MODE_BENCH
    dos_phosphor: str = dos.DEFAULT_PHOSPHOR
    dos_glow: int = dos.DEFAULT_GLOW
    dos_scanlines: bool = True
    dos_block_cursor: bool = True
    #: Whether the indicator set differs from the one Curie ships with.
    #:
    #: Deliberately not "is written down in config.yaml": the config layer
    #: merges ``DEFAULT_CONFIG`` under every read, so a key nobody has ever
    #: touched arrives with the same shape as one someone set to that exact
    #: value. "Unset" is therefore not a state this can see, and the honest
    #: reading of what it *can* see is "still on the shipped default".
    #:
    #: The DOS mode uses it for one decision — whether to offer its own native
    #: figure when it is switched on — and that decision is right under this
    #: reading: a console still on the panel's own set is one nobody has
    #: chosen a set for, and the moment the mode swaps it, the value stops
    #: being the default and the swap never happens again.
    indicators_explicit: bool = False

    @property
    def dos_mode(self) -> bool:
        """Whether the console opens as a phosphor terminal."""
        return self.skin_mode == dos.MODE_DOS

    def optics(self) -> dos.Optics:
        """The DOS mode's rendering parameters, as one object."""
        return dos.Optics(
            mode=self.skin_mode,
            phosphor=self.dos_phosphor,
            glow=self.dos_glow,
            scanlines=self.dos_scanlines,
            block_cursor=self.dos_block_cursor,
        )


@dataclass(frozen=True)
class PiperVoice:
    """One voice model found on disk."""

    name: str
    path: Path
    megabytes: float
    ready: bool

    @property
    def state(self) -> str:
        """What the settings table says about it, in one word."""
        return "ready" if self.ready else "no metadata"


# ── Reading ──────────────────────────────────────────────────────────────


def _config() -> dict:
    try:
        from curie_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception:
        return {}
    return config if isinstance(config, dict) else {}


def _dig(config: Any, *path: str) -> Any:
    node: Any = config
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def read_settings() -> BenchSettings:
    """Resolve every console preference, with defaults for anything unusable.

    Total by construction. This runs before the first frame is drawn, so
    anything it can raise is a console that will not start — and the reasons
    it could are all outside the user's sight: a config file mid-edit, an
    unreadable home, a value of a shape nothing here expects.
    """
    try:
        config = _config()
    except Exception:
        config = {}
    indicators = _dig(config, "ui", "indicators")
    known = isinstance(indicators, str) and indicators.strip().lower() in kit_names()
    if not known:
        indicators = DEFAULT_KIT
    explicit = known and str(indicators).strip().lower() != DEFAULT_KIT
    return BenchSettings(
        indicators=str(indicators).strip().lower(),
        indicators_explicit=explicit,
        voice_input=_flag(_dig(config, "ui", "voice_input")),
        speak_replies=_flag(_dig(config, "ui", "speak_replies")),
        tts_provider=_text(_dig(config, "tts", "provider")),
        piper_voice=_text(_dig(config, "tts", "piper", "voice")),
        piper_voices_dir=_text(_dig(config, "tts", "piper", "voices_dir")),
        skin=_text(_dig(config, "display", "skin")),
        skin_mode=dos.normalise_mode(_dig(config, "ui", "skin_mode")),
        dos_phosphor=dos.get_phosphor(_dig(config, "ui", "dos", "phosphor")).name,
        dos_glow=dos.clamp_glow(_dig(config, "ui", "dos", "glow")),
        # Both default to on: they are what makes the mode look like the thing
        # it is named after, and a reader who does not want them is one who
        # went looking for the switch.
        dos_scanlines=_flag(_dig(config, "ui", "dos", "scanlines"), default=True),
        dos_block_cursor=_flag(
            _dig(config, "ui", "dos", "block_cursor"), default=True
        ),
    )


def restore_active_skin(settings: BenchSettings | None = None) -> str:
    """Load the saved skin into the skin engine. Returns the name in force.

    The console is reached through ``curie ui``, which does not go through the
    classic CLI's start-up — and the skin engine is initialised from config
    *there*, in ``cli.py``. So the console began every run on the built-in
    default no matter what had been chosen, and a skin picked on the PANEL
    pane lasted exactly as long as the process did: applied, saved, and then
    ignored on the way back in.

    Total, like every other read here: an unreadable config, a skin that has
    since been deleted, or a skin engine that will not import all resolve to
    "whatever is already active" rather than stopping the console.
    """
    settings = settings if settings is not None else read_settings()
    try:
        from curie_cli.skin_engine import get_active_skin_name, set_active_skin
    except Exception:
        return ""
    wanted = settings.skin.strip()
    if not wanted:
        try:
            return str(get_active_skin_name() or "")
        except Exception:
            return ""
    try:
        set_active_skin(wanted)
        return wanted
    except Exception:
        # A skin that was removed since it was chosen. The console keeps the
        # default rather than refusing to start over an appearance setting.
        try:
            return str(get_active_skin_name() or "")
        except Exception:
            return ""


def _flag(value: Any, default: bool = False) -> bool:
    """A stored switch position, with a documented default when it is unset.

    ``default`` matters because not every switch is off out of the box:
    reading a missing scanline preference as "off" would open the DOS mode
    with the one effect its name promises already disabled.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return False


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


# ── Writing ──────────────────────────────────────────────────────────────


def write_setting(dotted_key: str, value: Any) -> str:
    """Persist one preference. Returns "" on success, or why it could not.

    A string rather than an exception because every caller is a keypress: the
    console has one place it puts a failure (the notice line) and no use for
    a traceback. A managed install, a read-only home and a config file
    someone is editing by hand all land here as a sentence.
    """
    try:
        from curie_cli.config import load_config, save_config
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return f"config unavailable ({type(exc).__name__})"

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return f"config could not be read ({type(exc).__name__}: {exc})"

    node = config if isinstance(config, dict) else {}
    parts = dotted_key.split(".")
    for key in parts[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[parts[-1]] = value

    # A managed install and a stripped managed key both make ``save_config``
    # print, and the console is occupying the alternate screen — a stray line
    # of stdout lands in the middle of the panel and stays there until the
    # next full repaint. Captured, and reported through the notice line like
    # everything else.
    said = io.StringIO()
    try:
        with redirect_stdout(said), redirect_stderr(said):
            # merge_existing keeps sections this console never looked at: the
            # console writes three keys and must not be the reason a
            # hand-edited gateway block disappears.
            save_config(config, merge_existing=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return f"could not be saved ({type(exc).__name__}: {exc})"
    spoke = " ".join(said.getvalue().split())
    if spoke and "managed" in spoke.lower():
        return spoke[:200]
    return ""


# ── Piper voices ─────────────────────────────────────────────────────────


def piper_voices_dir(settings: BenchSettings | None = None) -> Path:
    """The folder Piper voices are kept in, honouring an explicit override.

    Resolved without importing ``tools.tts_tool``: that module pulls in the
    whole synthesis stack, which is several seconds and a pile of optional
    dependencies to answer a question about a directory listing.
    """
    settings = settings if settings is not None else read_settings()
    if settings.piper_voices_dir:
        return Path(settings.piper_voices_dir).expanduser()
    try:
        from curie_constants import get_curie_dir

        return Path(get_curie_dir("cache/piper-voices", "piper_voices_cache"))
    except Exception:
        return Path.home() / ".curie" / "cache" / "piper-voices"


def list_piper_voices(settings: BenchSettings | None = None) -> List[PiperVoice]:
    """Every voice model in the folder, newest-looking name order.

    Recursive, because ``piper.download_voices`` will happily nest by
    language, and a voice the user can see in the folder but not in the
    settings table would read as a bug in the console.
    """
    folder = piper_voices_dir(settings)
    try:
        if not folder.is_dir():
            return []
        found = sorted(folder.rglob(f"*{VOICE_SUFFIX}"))
    except Exception:
        # An unreadable folder is an empty list with a reason the caller can
        # show; it is never a crash on the settings pane.
        return []

    voices: List[PiperVoice] = []
    for path in found:
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        metadata = path.with_name(path.name + ".json")
        voices.append(
            PiperVoice(
                name=path.name[: -len(VOICE_SUFFIX)],
                path=path,
                megabytes=round(size / (1024 * 1024), 1),
                ready=metadata.exists(),
            )
        )
    return voices


__all__ = [
    "BenchSettings",
    "KEY_DOS_BLOCK_CURSOR",
    "KEY_DOS_GLOW",
    "KEY_DOS_PHOSPHOR",
    "KEY_DOS_SCANLINES",
    "KEY_INDICATORS",
    "KEY_SKIN",
    "KEY_SKIN_MODE",
    "KEY_PIPER_VOICE",
    "KEY_PIPER_VOICES_DIR",
    "KEY_SPEAK_REPLIES",
    "KEY_TTS_PROVIDER",
    "KEY_VOICE_INPUT",
    "PiperVoice",
    "list_piper_voices",
    "piper_voices_dir",
    "read_settings",
    "restore_active_skin",
    "write_setting",
]
