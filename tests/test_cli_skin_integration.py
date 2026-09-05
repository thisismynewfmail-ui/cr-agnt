from types import SimpleNamespace
import yaml
from unittest.mock import MagicMock, patch

from cli import CurieCLI, _build_compact_banner, _rich_text_from_ansi
from curie_cli.skin_engine import get_active_skin, set_active_skin


def _make_cli_stub():
    cli = CurieCLI.__new__(CurieCLI)
    cli._sudo_state = None
    cli._secret_state = None
    cli._approval_state = None
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._command_running = False
    cli._agent_running = False
    cli._voice_recording = False
    cli._voice_processing = False
    cli._voice_mode = False
    cli._command_spinner_frame = lambda: "⟳"
    cli._tui_style_base = {
        "prompt": "#fff",
        "input-area": "#fff",
        "input-rule": "#aaa",
        "prompt-working": "#888 italic",
    }
    cli._app = SimpleNamespace(style=None)
    cli._invalidate = MagicMock()
    return cli


class TestCliSkinPromptIntegration:

    def test_prompt_fragments_use_the_skin_symbol(self):
        """``curie-vga`` uses ``►`` where the default uses ``▶``.

        The point is that the symbol comes from the active skin rather than a
        hard-coded fallback, so the skin under test has to be one whose
        symbol actually differs from the default's.
        """
        cli = _make_cli_stub()

        set_active_skin("curie-vga")
        assert cli._get_tui_prompt_fragments() == [("class:prompt", "► ")]

    def test_secret_prompt_fragments_preserve_secret_state(self):
        cli = _make_cli_stub()
        cli._secret_state = {"response_queue": object()}

        set_active_skin("curie-vga")
        assert cli._get_tui_prompt_fragments() == [("class:sudo-prompt", "🔑 ► ")]


    def test_build_tui_style_dict_uses_skin_overrides(self):
        cli = _make_cli_stub()

        set_active_skin("ares")
        skin = get_active_skin()
        style_dict = cli._build_tui_style_dict()

        assert style_dict["prompt"] == skin.get_color("prompt")
        assert style_dict["input-rule"] == skin.get_color("input_rule")
        assert style_dict["prompt-working"] == f"{skin.get_color('banner_dim')} italic"
        assert style_dict["status-bar"] == (
            f"bg:{skin.get_color('status_bar_bg')} {skin.get_color('status_bar_text')}"
        )
        assert style_dict["approval-title"] == f"{skin.get_color('ui_warn')} bold"

    def test_apply_tui_skin_style_updates_running_app(self):
        cli = _make_cli_stub()

        set_active_skin("curie-vga")
        assert cli._apply_tui_skin_style() is True
        assert cli._app.style is not None
        cli._invalidate.assert_called_once_with(min_interval=0.0)

    def test_handle_skin_command_refreshes_live_tui(self, capsys):
        cli = _make_cli_stub()

        with patch("cli.save_config_value", return_value=True):
            cli._handle_skin_command("/skin curie-vga")

        output = capsys.readouterr().out
        assert "Skin set to: curie-vga (saved)" in output
        assert "Prompt + TUI colors updated." in output
        assert cli._app.style is not None


class TestCompactBannerSkinIntegration:
    """The banner reads its wording and colours from the active skin.

    Every skin Curie ships says "Curie Agent", so a shipped skin cannot show
    that the name is read rather than hard-coded. A *user* skin can — and
    that is the extension point the invariant exists to protect, since a user
    who renames the agent in their own skin should see it in the banner.
    """

    @staticmethod
    def _user_skin(tmp_path, monkeypatch, **branding):
        import curie_cli.skin_engine as skin_engine

        skins = tmp_path / "skins"
        skins.mkdir(parents=True, exist_ok=True)
        (skins / "benchmark.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "benchmark",
                    "branding": {"agent_name": "Bench Instrument", **branding},
                    "colors": {
                        "banner_border": "#123456",
                        "banner_title": "#654321",
                        "banner_dim": "#abcdef",
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(skin_engine, "_skins_dir", lambda: skins)
        return skin_engine.load_skin("benchmark")

    def test_the_banner_uses_the_skin_name_not_a_hard_coded_one(
        self, tmp_path, monkeypatch
    ):
        skin = self._user_skin(tmp_path, monkeypatch)
        # ``_build_compact_banner`` imports ``get_active_skin`` inside the
        # function, so the name is resolved from the module at call time —
        # patching the function's globals would never be seen.
        monkeypatch.setattr(
            "curie_cli.skin_engine.get_active_skin", lambda: skin
        )

        with patch("cli.shutil.get_terminal_size", return_value=SimpleNamespace(columns=90)), \
             patch.dict(_build_compact_banner.__globals__, {
                 "format_banner_version_label": lambda: "Curie Agent v0.1.0 (test)",
             }):
            banner = _build_compact_banner()

        assert "Bench Instrument" in banner
        assert "NOUS CURIE" not in banner

    def test_the_banner_uses_the_skin_colours(self, tmp_path, monkeypatch):
        skin = self._user_skin(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "curie_cli.skin_engine.get_active_skin", lambda: skin
        )

        with patch("cli.shutil.get_terminal_size", return_value=SimpleNamespace(columns=90)), \
             patch.dict(_build_compact_banner.__globals__, {
                 "format_banner_version_label": lambda: "Curie Agent v0.1.0 (test)",
             }):
            banner = _build_compact_banner()

        assert skin.get_color("banner_border") in banner
        assert skin.get_color("banner_title") in banner
        assert skin.get_color("banner_dim") in banner


class TestAnsiRichTextHelper:
    def test_preserves_literal_brackets(self):
        text = _rich_text_from_ansi("[notatag] literal")
        assert text.plain == "[notatag] literal"

