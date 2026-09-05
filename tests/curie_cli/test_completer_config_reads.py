"""Measured-work pins for the slash-completer config reads.

The /tools completer runs on every keystroke while the user types the
command (complete_while_typing). It used to re-read + re-parse the full
config on every keypress, paying load_config()'s defensive deepcopy (~345us
tax). This pin holds the per-keystroke cost down: _tools_completions uses
the read-only loader, which skips the deepcopy.

(The /personality completer had the same problem, solved with an
mtime-keyed memo of the personalities source. Both the completer and the
command are gone — SOUL.md is the only description of the agent's voice —
so those pins went with them.)
"""

from __future__ import annotations

from unittest.mock import patch

import curie_cli.commands as commands_mod


class TestToolsCompletionsReadonlyConfig:
    def test_uses_readonly_loader(self):
        """_tools_completions must not pay the defensive deepcopy.

        The completer only reads the config (toolset enable state + MCP
        server names). Using load_config_readonly() skips the ~345us
        deepcopy that load_config() applies on every cache hit — a
        per-keystroke cost while completing /tools enable|disable.
        """
        calls = {"deepcopy": 0, "readonly": 0}

        def counting_deepcopy(*a, **k):
            calls["deepcopy"] += 1
            return {}

        def counting_readonly(*a, **k):
            calls["readonly"] += 1
            return {}

        # The completer imports the loader inside the function, so patch the
        # source module. Portable-MCP lookup is stubbed because it triggers
        # one-time plugin discovery (which legitimately calls load_config
        # during process init) — this test asserts on the completer's own
        # per-keystroke reads, not discovery's one-off startup reads.
        with patch("curie_cli.config.load_config", counting_deepcopy), \
             patch("curie_cli.config.load_config_readonly", counting_readonly), \
             patch("curie_cli.plugins.get_portable_mcp_server_names_nowait", lambda: set()), \
             patch("curie_cli.tools_config._get_plugin_toolset_keys", lambda: set()), \
             patch("curie_cli.tools_config._homeassistant_credentials_present", lambda: False), \
             patch("curie_cli.tools_config._xai_credentials_present", lambda: False):
            list(commands_mod.SlashCommandCompleter._tools_completions("enable ", "enable "))

        assert calls["readonly"] == 1, "completer should use the readonly loader"
        assert calls["deepcopy"] == 0, (
            "completer must not call the deepcopy loader on a read-only path"
        )
