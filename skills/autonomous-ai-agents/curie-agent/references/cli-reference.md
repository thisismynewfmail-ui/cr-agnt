# Curie CLI Reference

Live sources when anything looks stale: `curie --help`, `curie <command> --help`,
https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/cli-commands.md

### Global Flags

```
curie [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --unlock                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
curie chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
curie setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
curie model                Interactive model/provider picker
curie fallback [add|remove|list]  Fallback provider chain
curie config [show|edit|get|set|unset|path|env-path|check|migrate]
curie login / logout       OAuth sign-in / clear stored auth
curie doctor [--fix]       Check dependencies and config
curie status [--all]       Component status
```

### Tools & Skills

```
curie tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

curie skills list|browse|search QUERY|inspect ID
curie skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
curie skills config        Enable/disable skills per platform
curie skills check|update|uninstall|publish PATH
curie skills tap add REPO  Add a GitHub repo as a skill source
curie bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
curie mcp add NAME (--url or --command) | remove | list | test NAME
curie mcp catalog | install NAME     Curated catalog install
curie mcp configure NAME             Toggle tool selection
curie mcp serve                      Run Curie as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
curie gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `curie photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs/user-guide/messaging

### Sessions

```
curie sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
curie cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
curie webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
curie profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
curie profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
curie auth                 Interactive credential manager
curie auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
curie auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
curie desktop / gui        Native desktop app
curie dashboard            Web admin panel + embedded chat (--stop / --status)
curie proxy                OpenAI-compatible local proxy backed by an OAuth provider
curie portal               Quick setup / sign in via Nous Portal
curie kanban <verb>        Multi-agent work-queue board
curie project              Named multi-folder workspaces
curie skin list|use|set    Switch/tweak skins (see references/themes.md)
curie pets <verb>          Pet mascots (see references/petdex.md)
curie memory setup|status|off|reset   Memory provider
curie secrets bitwarden|onepassword   External secret stores
curie moa                  Mixture-of-Agents slots
curie hooks / security / backup / import / checkpoints / console
curie logs [-f] [errors]   View agent/error logs
curie send                 One-off message through a gateway platform
curie pairing / plugins / insights / journey / computer-use
curie acp                  ACP server (IDE integration)
curie completion bash|zsh|fish
curie update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `curie photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `curie config edit` · [Configuration docs](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/configuration.md) |
| Tools / toolsets | `curie tools list` · [Tools reference](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/tools-reference.md) |
| Skills catalog | `curie skills browse` · [Skills catalog](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/skills-catalog.md) |
| Provider setup | `curie model` · [Providers guide](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/integrations/providers.md) |
| Env variables | `curie config env-path` · [Env vars reference](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/environment-variables.md) |
| Gateway logs | `~/.curie/logs/gateway.log` (or `curie logs`) |
| Sessions | `curie sessions browse` (reads state.db) |
