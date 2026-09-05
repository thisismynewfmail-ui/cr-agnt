<p align="center">
  <img src="assets/banner.png" alt="Curie Agent" width="100%">
</p>

# Curie Agent ☤
<p align="center">
  <a href="https://github.com/thisismynewfmail-ui/Cur-Agnt/">Curie Agent</a> | <a href="https://github.com/thisismynewfmail-ui/Cur-Agnt/">Curie Desktop</a>
</p>
<p align="center">
  <a href="https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs"><img src="https://img.shields.io/badge/Docs-curie--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://nousresearch.com"><img src="https://img.shields.io/badge/Built%20by-Nous%20Research-blueviolet?style=for-the-badge" alt="Built by Nous Research"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
</p>

**The self-improving AI agent built by [Nous Research](https://nousresearch.com).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [Nous Portal](https://portal.nousresearch.com), OpenRouter, OpenAI, your own endpoint, and [many others](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/integrations/providers.md). Switch with `curie model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Seven terminal backends — local, Docker, SSH, Singularity, Modal, Daytona, and Vercel Sandbox. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

Curie installs to `~/.curie` and is completely independent of any other agent
on the machine. It shares no directory, environment variable, systemd unit or
launchd label with a Hermes install, so the two can sit side by side without
either one touching the other's sessions, config or credentials.

### Linux, macOS, WSL2, Termux

```bash
curl -fsSL https://raw.githubusercontent.com/thisismynewfmail-ui/cru/main/scripts/install.sh | bash
```

### Windows (native, PowerShell)

> **Heads up:** Native Windows runs Curie without WSL — CLI, gateway, TUI, and tools all work natively. If you'd rather use WSL2, the Linux/macOS one-liner above works there too. Found a bug? Please [file issues](https://github.com/thisismynewfmail-ui/Cur-Agnt/issues).

Run this in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/thisismynewfmail-ui/cru/main/scripts/install.ps1)
```

The installer handles everything: uv, Python 3.11, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\curie\git` — no admin required, completely isolated from any system Git install). Curie uses this bundled Git Bash to run shell commands.

If you already have Git installed, the installer detects it and uses that instead. Otherwise a ~45MB MinGit download is all you need — it won't touch or interfere with any system Git.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/getting-started/termux.md). On Termux, Curie installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is fully supported — the PowerShell one-liner above installs everything. If you'd rather use WSL2, the Linux command works there too. Native Windows install lives under `%LOCALAPPDATA%\curie`; WSL2 installs under `~/.curie` as on Linux.

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
curie              # start chatting!
```

### Troubleshooting

#### Windows Defender or antivirus flags `uv.exe` as malware

If your antivirus (Bitdefender, Windows Defender, etc.) quarantines `uv.exe` from the Curie `bin` folder (`%LOCALAPPDATA%\curie\bin\uv.exe`), this is a **false positive**. The file is Astral's `uv` — the Rust Python package manager Curie bundles to manage its Python environment. ML-based antivirus engines commonly flag unsigned Rust binaries that download and install packages.

**To verify your copy is authentic:**

```powershell
# Install GitHub CLI if needed
winget install --id GitHub.cli

# Login to GitHub
gh auth login

# Run verification
$uv = "$env:LOCALAPPDATA\curie\bin\uv.exe"
$ver = (& $uv --version).Split(' ')[1]
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zip = "$env:TEMP\uv.zip"
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$ver/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip -UseBasicParsing
gh attestation verify $zip --repo astral-sh/uv
Expand-Archive $zip "$env:TEMP\uv_x" -Force
(Get-FileHash "$env:TEMP\uv_x\uv.exe").Hash -eq (Get-FileHash $uv).Hash
```

If attestation says "Verification succeeded" and the last line prints `True`, you're good.

**To whitelist Curie:**
- **Windows Defender:** Run PowerShell as Admin → `Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\curie\bin"`
- **Bitdefender:** Add an exception in the Bitdefender console (Protection > Antivirus > Settings > Manage Exceptions)
- Whitelist the **folder**, not the file hash — Curie updates `uv` and the hash changes every version

For more context, see the upstream Astral reports: [astral-sh/uv#13553](https://github.com/astral-sh/uv/issues/13553), [astral-sh/uv#15011](https://github.com/astral-sh/uv/issues/15011), [astral-sh/uv#10079](https://github.com/astral-sh/uv/issues/10079).

---

## Getting Started

```bash
curie              # Interactive CLI — start a conversation
curie model        # Choose your LLM provider and model
curie tools        # Configure which tools are enabled
curie config set   # Set individual config values
curie config get   # Print individual config values
curie gateway      # Start the messaging gateway (Telegram, Discord, etc.)
curie setup        # Run the full setup wizard (configures everything at once)
curie claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
curie update       # Update to the latest version
curie doctor       # Diagnose any issues
```

📖 **[Full documentation →](https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs)**

---

## Skip the API-key collection — Nous Portal

Curie works with whatever provider you want — that's not changing. But if you'd rather not collect five separate API keys for the model, web search, image generation, TTS, and a cloud browser, **[Nous Portal](https://portal.nousresearch.com)** covers all of them under one subscription:

- **300+ models** — pick any of them with `/model <name>`
- **Tool Gateway** — web search (Firecrawl), image generation (FAL), text-to-speech (OpenAI), cloud browser (Browser Use), all routed through your sub. No extra accounts.

One command from a fresh install:

```bash
curie setup --portal
```

That logs you in via OAuth, sets Nous as your provider, and turns on the Tool Gateway. Check what's wired up any time with `curie portal info`. Full details on the [Tool Gateway docs page](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/tool-gateway.md).

You can still bring your own keys per-tool whenever you want — the gateway is per-backend, not all-or-nothing.

---

## CLI vs Messaging Quick Reference

Curie has two entry points: start the terminal UI with `curie`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action                         | CLI                                           | Messaging platforms                                                              |
| ------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| Start chatting                 | `curie`                                      | Run `curie gateway setup` + `curie gateway start`, then send the bot a message |
| Start fresh conversation       | `/new` or `/reset`                            | `/new` or `/reset`                                                               |
| Change model                   | `/model [provider:model]`                     | `/model [provider:model]`                                                        |
| Open the bench console         | `curie ui`                                    | —                                                                                |
| Change the look                | `/skin [name]`                                | —                                                                                |
| Retry or undo the last turn    | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                        |
| Browse skills                  | `/skills` or `/<skill-name>`                  | `/<skill-name>`                                                                  |
| Interrupt current work         | `Ctrl+C` or send a new message                | `/stop` or send a new message                                                    |
| Platform-specific status       | `/platforms`                                  | `/status`, `/sethome`                                                            |

For the full command lists, see the [CLI guide](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/cli.md) and the [Messaging Gateway guide](https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs/user-guide/messaging).

---

## Documentation

All documentation lives at **[github.com/thisismynewfmail-ui/Cur-Agnt/docs](https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs)**:

| Section                                                                                             | What's Covered                                             |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| [Quickstart](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/getting-started/quickstart.md)                 | Install → setup → first conversation in 2 minutes          |
| [CLI Usage](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/cli.md)                              | Commands, keybindings, sessions, the bench console          |
| [Configuration](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/configuration.md)                | Config file, providers, models, all options                |
| [Messaging Gateway](https://github.com/thisismynewfmail-ui/Cur-Agnt/tree/main/website/docs/user-guide/messaging)                | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/security.md)                          | Command approval, DM pairing, container isolation          |
| [Tools & Toolsets](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/tools.md)            | 40+ tools, toolset system, terminal backends               |
| [Skills System](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/skills.md)              | Procedural memory, Skills Hub, creating skills             |
| [Memory](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/memory.md)                     | Persistent memory, user profiles, best practices           |
| [MCP Integration](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/mcp.md)               | Connect any MCP server for extended capabilities           |
| [Cron Scheduling](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/cron.md)              | Scheduled tasks with platform delivery                     |
| [Context Files](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/user-guide/features/context-files.md)       | Project context that shapes every conversation             |
| [Architecture](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/developer-guide/architecture.md)             | Project structure, agent loop, key classes                 |
| [Contributing](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/developer-guide/contributing.md)             | Development setup, PR process, code style                  |
| [CLI Reference](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/cli-commands.md)                  | All commands and flags                                     |
| [Environment Variables](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/reference/environment-variables.md) | Complete env var reference                                 |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Curie can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`curie setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
curie claw migrate              # Interactive migration (full preset)
curie claw migrate --dry-run    # Preview what would be migrated
curie claw migrate --preset user-data   # Migrate without secrets
curie claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:

- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.curie/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `curie claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://github.com/thisismynewfmail-ui/Cur-Agnt/blob/main/website/docs/developer-guide/contributing.md) for development setup, code style, and PR process.

Quick start for contributors — use the standard installer, then work from the
full git checkout it creates at `$CURIE_HOME/curie-agent` (usually
`~/.curie/curie-agent`). This matches the layout used by `curie update`, the
managed venv, lazy dependencies, gateway, and docs tooling.

```bash
curl -fsSL https://raw.githubusercontent.com/thisismynewfmail-ui/cru/main/scripts/install.sh | bash
cd "${CURIE_HOME:-$HOME/.curie}/curie-agent"
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

Manual clone fallback (for throwaway clones/CI where you intentionally do not
want the managed install layout):

Create the venv outside the cloned source tree — a venv inside the directory
the agent operates from can be wiped by a relative-path command the agent runs
against its own checkout, destroying the running runtime mid-session.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv ~/.curie/venvs/curie-dev --python 3.11
source ~/.curie/venvs/curie-dev/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 💬 [Discord](https://discord.gg/NousResearch)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/thisismynewfmail-ui/Cur-Agnt/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Curie and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [CurieClaw](https://github.com/AaronWong1999/curieclaw) — Community WeChat bridge: Run Curie Agent and OpenClaw on the same WeChat account.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Nous Research](https://nousresearch.com).
