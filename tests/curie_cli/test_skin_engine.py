"""Tests for curie_cli.skin_engine — the data-driven skin/theme system."""

import pytest


@pytest.fixture(autouse=True)
def reset_skin_state():
    """Reset skin engine state between tests."""
    from curie_cli import skin_engine
    skin_engine._active_skin = None
    skin_engine._active_skin_name = "default"
    yield
    skin_engine._active_skin = None
    skin_engine._active_skin_name = "default"


class TestSkinConfig:
    def test_default_skin_has_required_fields(self):
        from curie_cli.skin_engine import load_skin
        skin = load_skin("default")
        assert skin.name == "default"
        assert skin.tool_prefix == "│"
        assert "banner_title" in skin.colors
        assert "banner_border" in skin.colors
        assert "agent_name" in skin.branding


    def test_get_spinner_wings_for_default(self):
        """The bench skin ships its own wings; every pair must be well-formed.

        This used to assert the default had *no* spinner customisation, which
        was true when the default was the inherited base for every other
        skin. The bench is a designed palette with its own spinner, so the
        contract worth holding is the shape: two entries per pair.
        """
        from curie_cli.skin_engine import load_skin
        skin = load_skin("default")
        wings = skin.get_spinner_wings()
        assert wings, "the bench skin declares spinner wings"
        assert all(len(pair) == 2 for pair in wings), wings
        assert all(isinstance(side, str) and side for pair in wings for side in pair)


class TestBuiltinSkins:
    def test_vga_skin_loads(self):
        from curie_cli.skin_engine import load_skin
        skin = load_skin("curie-vga")
        assert skin.name == "curie-vga"
        assert skin.tool_prefix == "║"
        # VGA identity: the border is a cyan from the sixteen (exact values
        # are owned by the palette audit in test_skin_palettes.py, which
        # enforces the contrast floors — don't pin literals here).
        border = skin.get_color("banner_border")
        r, g, b = (int(border[i:i + 2], 16) for i in (1, 3, 5))
        assert b >= g > r, (
            f"curie-vga border is not a VGA cyan: {border}"
        )
        assert skin.get_color("response_border") == "#FFFF55"  # VGA yellow
        assert skin.get_color("session_label") == "#FFFF55"   # VGA yellow
        assert skin.get_color("session_border") == "#AAAAAA"  # VGA light gray
        assert skin.get_branding("agent_name") == "Curie Agent"

    def test_vga_has_spinner_customization(self):
        from curie_cli.skin_engine import load_skin
        skin = load_skin("curie-vga")
        wings = skin.get_spinner_wings()
        assert len(wings) > 0
        assert isinstance(wings[0], tuple)
        assert len(wings[0]) == 2








class TestSkinManagement:
    def test_set_active_skin(self):
        from curie_cli.skin_engine import set_active_skin, get_active_skin, get_active_skin_name
        skin = set_active_skin("curie-vga")
        assert skin.name == "curie-vga"
        assert get_active_skin_name() == "curie-vga"
        assert get_active_skin().name == "curie-vga"


    def test_list_skins_includes_builtins(self):
        from curie_cli.skin_engine import list_skins
        skins = list_skins()
        names = [s["name"] for s in skins]
        assert "default" in names
        assert "curie-vga" in names
        assert "mono" in names
        assert "slate" in names
        assert "daylight" in names
        assert "warm-lightmode" in names
        for s in skins:
            assert "source" in s
            assert s["source"] == "builtin"




class TestUserSkins:
    def test_load_user_skin_from_yaml(self, tmp_path, monkeypatch):
        from curie_cli.skin_engine import load_skin
        # Create a user skin YAML
        skins_dir = tmp_path / "skins"
        skins_dir.mkdir()
        skin_file = skins_dir / "custom.yaml"
        skin_data = {
            "name": "custom",
            "description": "A custom test skin",
            "colors": {"banner_title": "#FF0000"},
            "branding": {"agent_name": "Custom Agent"},
            "tool_prefix": "▸",
        }
        import yaml
        skin_file.write_text(yaml.dump(skin_data))

        # Patch skins dir
        monkeypatch.setattr("curie_cli.skin_engine._skins_dir", lambda: skins_dir)

        skin = load_skin("custom")
        assert skin.name == "custom"
        assert skin.get_color("banner_title") == "#FF0000"
        assert skin.get_branding("agent_name") == "Custom Agent"
        assert skin.tool_prefix == "▸"
        # Should inherit defaults for unspecified colors
        assert skin.get_color("banner_border") == "#8C7A5B"  # from default

    def test_load_user_skin_invalid_section_types_fall_back_to_defaults(self, tmp_path, monkeypatch):
        from curie_cli.skin_engine import load_skin

        skins_dir = tmp_path / "skins"
        skins_dir.mkdir()
        import yaml

        (skins_dir / "broken.yaml").write_text(
            yaml.dump(
                {
                    "name": "broken",
                    "colors": ["not", "a", "mapping"],
                    "spinner": "invalid",
                    "branding": ["also", "invalid"],
                    "tool_emojis": ["invalid"],
                    "tool_prefix": "!",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("curie_cli.skin_engine._skins_dir", lambda: skins_dir)

        skin = load_skin("broken")

        assert skin.name == "broken"
        assert skin.get_color("banner_title") == "#8A3B12"
        assert skin.get_branding("agent_name") == "Curie Agent"
        # Invalid sections fall back to the *default* skin, which now ships
        # its own spinner — so the contract is "inherited", not "empty".
        from curie_cli.skin_engine import load_skin as _load_skin

        assert skin.spinner.get("waiting_faces", []) == _load_skin(
            "default"
        ).spinner.get("waiting_faces", [])
        assert skin.tool_emojis == {}
        assert skin.tool_prefix == "!"

    def test_list_skins_includes_user_skins(self, tmp_path, monkeypatch):
        from curie_cli.skin_engine import list_skins
        skins_dir = tmp_path / "skins"
        skins_dir.mkdir()
        import yaml
        (skins_dir / "pirate.yaml").write_text(yaml.dump({
            "name": "pirate",
            "description": "Arr matey",
        }))
        monkeypatch.setattr("curie_cli.skin_engine._skins_dir", lambda: skins_dir)

        skins = list_skins()
        names = [s["name"] for s in skins]
        assert "pirate" in names
        pirate = [s for s in skins if s["name"] == "pirate"][0]
        assert pirate["source"] == "user"


class TestDisplayIntegration:


    def test_tool_message_uses_skin_prefix(self):
        from curie_cli.skin_engine import set_active_skin
        from agent.display import get_cute_tool_message
        set_active_skin("curie-vga")
        msg = get_cute_tool_message("terminal", {"command": "ls"}, 0.5)
        assert msg.startswith("║")
        assert "┊" not in msg


class TestCliBrandingHelpers:


    def test_active_goodbye_curie_vga(self):
        from curie_cli.skin_engine import set_active_skin, get_active_goodbye

        set_active_skin("curie-vga")
        assert get_active_goodbye() == "Bench secured. Logbook closed. ▮"

    def test_prompt_toolkit_style_overrides_cover_tui_classes(self):
        from curie_cli.skin_engine import set_active_skin, get_prompt_toolkit_style_overrides
        set_active_skin("curie-vga")
        overrides = get_prompt_toolkit_style_overrides()
        required = {
            "input-area",
            "placeholder",
            "prompt",
            "prompt-working",
            "hint",
            "status-bar",
            "status-bar-strong",
            "status-bar-dim",
            "status-bar-good",
            "status-bar-warn",
            "status-bar-bad",
            "status-bar-critical",
            "input-rule",
            "image-badge",
            "completion-menu",
            "completion-menu.completion",
            "completion-menu.completion.current",
            "completion-menu.meta.completion",
            "completion-menu.meta.completion.current",
            "status-bar",
            "status-bar-strong",
            "status-bar-dim",
            "status-bar-good",
            "status-bar-warn",
            "status-bar-bad",
            "status-bar-critical",
            "voice-status",
            "voice-status-recording",
            "clarify-border",
            "clarify-title",
            "clarify-question",
            "clarify-choice",
            "clarify-selected",
            "clarify-active-other",
            "clarify-countdown",
            "sudo-prompt",
            "sudo-border",
            "sudo-title",
            "sudo-text",
            "approval-border",
            "approval-title",
            "approval-desc",
            "approval-cmd",
            "approval-choice",
            "approval-selected",
        }
        assert required.issubset(overrides.keys())

    def test_prompt_toolkit_style_overrides_use_skin_colors(self):
        from curie_cli.skin_engine import (
            set_active_skin,
            get_active_skin,
            get_prompt_toolkit_style_overrides,
        )

        set_active_skin("curie-vga")
        skin = get_active_skin()
        overrides = get_prompt_toolkit_style_overrides()
        assert overrides["prompt"] == skin.get_color("prompt")
        assert overrides["input-rule"] == skin.get_color("input_rule")
        assert overrides["status-bar"] == (
            f"bg:{skin.get_color('status_bar_bg')} {skin.get_color('status_bar_text')}"
        )
        assert overrides["status-bar-strong"] == (
            f"bg:{skin.get_color('status_bar_bg')} {skin.get_color('status_bar_strong')} bold"
        )
        assert overrides["status-bar-critical"] == (
            f"bg:{skin.get_color('status_bar_bg')} {skin.get_color('status_bar_critical')} bold"
        )
        assert overrides["clarify-title"] == f"{skin.get_color('banner_title')} bold"
        assert overrides["sudo-prompt"] == f"{skin.get_color('ui_error')} bold"
        assert overrides["approval-title"] == f"{skin.get_color('ui_warn')} bold"

        set_active_skin("daylight")
        skin = get_active_skin()
        overrides = get_prompt_toolkit_style_overrides()
        assert overrides["status-bar"] == f"bg:{skin.get_color('status_bar_bg')} {skin.get_color('banner_text')}"
        assert overrides["voice-status"] == f"bg:{skin.get_color('voice_status_bg')} {skin.get_color('ui_label')}"
