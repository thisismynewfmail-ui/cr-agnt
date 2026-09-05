---
sidebar_position: 3
title: "Nix & NixOS Setup"
description: "Install and deploy Curie Agent with Nix — from quick `nix run` to fully declarative NixOS module with container mode"
---

# Nix & NixOS Setup

:::warning Tier 2 platform
Nix and NixOS are [Tier 2 platforms](./platform-support.md#tier-2). The flake and NixOS module documented here are maintained on a best-effort basis only. Commits to `main` may break these packages at any point in time.

For a supported setup, use one of the standard [installation](./installation.md) paths - either Docker or an FHS environment.
:::

Curie Agent ships a Nix flake, a NixOS module, and a Home Manager module.

| Level | Who it's for | What you get |
|-------|-------------|--------------|
| **`nix run` / `nix profile install`** | Any Nix user (macOS, Linux) | Pre-built binary with all deps — then use the standard CLI workflow |
| **Home Manager module** | An agent for one person, on any distribution or on macOS | Declarative configuration and a user service, without root |
| **NixOS module (native)** | NixOS server deployments | Declarative config, hardened systemd service, managed secrets |
| **NixOS module (container)** | Agents that need self-modification | Everything above, plus a persistent Ubuntu container where the agent can `apt`/`pip`/`npm install` |

:::info What's different from the standard install
The `curl | bash` installer manages Python, Node, and dependencies itself. The Nix flake replaces all of that — every Python dependency is a Nix derivation built by [uv2nix](https://github.com/pyproject-nix/uv2nix), and runtime tools (Node.js, git, ripgrep, ffmpeg) are wrapped into the binary's PATH. There is no runtime pip, no venv activation, no `npm install`.

**For non-NixOS users**, this only changes the install step. Everything after (`curie setup`, `curie gateway install`, config editing) works identically to the standard install.

**For NixOS module users**, the entire lifecycle is different: configuration lives in `configuration.nix`, secrets go through sops-nix/agenix, the service is a systemd unit, and CLI config commands are blocked. You manage curie the same way you manage any other NixOS service.
:::

## Prerequisites

- **Nix with flakes enabled** — [Determinate Nix](https://install.determinate.systems) recommended (enables flakes by default)
- **API keys** for the services you want to use (at minimum: an OpenRouter or Anthropic key)

---

## Quick Start (Any Nix User)

No clone needed. Nix fetches, builds, and runs everything:

```bash
# Run the desktop app
nix run github:thisismynewfmail-ui/Cur-Agnt#desktop

# Or install persistently
nix profile install github:thisismynewfmail-ui/Cur-Agnt#desktop

# run the tui
nix run github:thisismynewfmail-ui/Cur-Agnt -- setup
nix run github:thisismynewfmail-ui/Cur-Agnt -- --tui

# or install it in your profile
nix profile install github:thisismynewfmail-ui/Cur-Agnt
curie setup
curie --tui
```

After `nix profile install`, `curie`, `curie-agent`, and `curie-acp` are on your PATH. From here, the workflow is identical to the [standard installation](./installation.md) — `curie setup` walks you through provider selection, `curie gateway install` sets up a launchd (macOS) or systemd user service, and config lives in `~/.curie/`.

:::warning Messaging platforms (Discord, Telegram, Slack)
The default package includes ALL libraries curie-agent might need. if you want a smaller variant, check the other flake outputs. 

The `default` package adds ~700 MB to the closure. If you only need messaging platforms, `#messaging` adds just ~33 MB.

:::

<details>
<summary><strong>Running from a local clone</strong></summary>

```bash
git clone https://github.com/thisismynewfmail-ui/Cur-Agnt.git
cd curie-agent
nix develop
curie setup
```

</details>

---

## NixOS Module

The flake exports `nixosModules.default` — a full NixOS service module that declaratively manages user creation, directories, config generation, secrets, documents, and service lifecycle.

:::note
This module needs NixOS. Curie is an agent for one person. If you want an agent for one person and not a system service, use the [Home Manager module](#home-manager-module). That module runs on NixOS and on each other system that Home Manager supports.
:::

### Add the Flake Input

```nix
# /etc/nixos/flake.nix (or your system flake)
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    curie-agent.url = "github:thisismynewfmail-ui/Cur-Agnt";
  };

  outputs = { nixpkgs, curie-agent, ... }: {
    nixosConfigurations.your-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        curie-agent.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

### Minimal Configuration

```nix
# configuration.nix
{ config, ... }: {
  services.curie-agent = {
    enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."curie-env".path ];
    addToSystemPackages = true;
  };
}
```

That's it. `nixos-rebuild switch` creates the `curie` user, generates `config.yaml`, wires up secrets, and starts the gateway — a long-running service that connects the agent to messaging platforms (Telegram, Discord, etc.) and listens for incoming messages.

:::warning Secrets are required
The `environmentFiles` line above assumes you have [sops-nix](https://github.com/Mic92/sops-nix) or [agenix](https://github.com/ryantm/agenix) configured. The file should contain at least one LLM provider key (e.g., `OPENROUTER_API_KEY=sk-or-...`). See [Secrets Management](#secrets-management) for full setup. If you don't have a secrets manager yet, you can use a plain file as a starting point — just ensure it's not world-readable:

```bash
echo "OPENROUTER_API_KEY=sk-or-your-key" | sudo install -m 0600 -o curie /dev/stdin /var/lib/curie/env
```

```nix
services.curie-agent.environmentFiles = [ "/var/lib/curie/env" ];
```
:::

:::tip addToSystemPackages
Setting `addToSystemPackages = true` does two things: puts the `curie` CLI on your system PATH **and** sets `CURIE_HOME` system-wide so the interactive CLI shares state (sessions, skills, cron) with the gateway service. Without it, running `curie` in your shell creates a separate `~/.curie/` directory.
:::

### Container-aware CLI

:::info
When `container.enable = true` and `addToSystemPackages = true`, **every** `curie` command on the host automatically routes into the managed container. This means your interactive CLI session runs inside the same environment as the gateway service — with access to all container-installed packages and tools.

- The routing is transparent: `curie chat`, `curie sessions list`, `curie --version`, etc. all exec into the container under the hood
- All CLI flags are forwarded as-is
- If the container isn't running, the CLI retries briefly (5s with a spinner for interactive use, 10s silently for scripts) then fails with a clear error — no silent fallback
- For developers working on the curie codebase, set `CURIE_DEV=1` to bypass container routing and run the local checkout directly

Set `container.hostUsers` to create a `~/.curie` symlink to the service state directory, so the host CLI and the container share sessions, config, and memories:

```nix
services.curie-agent = {
  container.enable = true;
  container.hostUsers = [ "your-username" ];
  addToSystemPackages = true;
};
```

Users listed in `hostUsers` are automatically added to the `curie` group for file permission access.

**Podman users:** The NixOS service runs the container as root. Docker users get access via the `docker` group socket, but Podman's rootful containers require sudo. Grant passwordless sudo for your container runtime:

```nix
security.sudo.extraRules = [{
  users = [ "your-username" ];
  commands = [{
    command = "/run/current-system/sw/bin/podman";
    options = [ "NOPASSWD" ];
  }];
}];
```

The CLI auto-detects when sudo is needed and uses it transparently. Without this, you'll need to run `sudo curie chat` manually.
:::

### Verify It Works

After `nixos-rebuild switch`, check that the service is running:

```bash
# Check service status
systemctl status curie-agent

# Watch logs (Ctrl+C to stop)
journalctl -u curie-agent -f

# If addToSystemPackages is true, test the CLI
curie --version
curie config       # shows the generated config
```

### Choosing a Deployment Mode

The module supports two modes, controlled by `container.enable`:

| | **Native** (default) | **Container** |
|---|---|---|
| How it runs | Hardened systemd service on the host | Persistent Ubuntu container with `/nix/store` bind-mounted |
| Security | `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp` | Container isolation, runs as unprivileged user inside |
| Agent can self-install packages | No — only tools on the Nix-provided PATH | Yes — `apt`, `pip`, `npm` installs persist across restarts |
| Config surface | Same | Same |
| When to choose | Standard deployments, maximum security, reproducibility | Agent needs runtime package installation, mutable environment, experimental tools |

To enable container mode, add one line:

```nix
{
  services.curie-agent = {
    enable = true;
    container.enable = true;
    # ... rest of config is identical
  };
}
```

:::info
Container mode auto-enables `virtualisation.docker.enable` via `mkDefault`. If you use Podman instead, set `container.backend = "podman"` and `virtualisation.docker.enable = false`.
:::

---

## Configuration

### Declarative Settings

The `settings` option accepts an arbitrary attrset that is rendered as `config.yaml`. It supports deep merging across multiple module definitions (via `lib.recursiveUpdate`), so you can split config across files:

```nix
# base.nix
services.curie-agent.settings = {
  model.default = "anthropic/claude-sonnet-4";
  toolsets = [ "all" ];
  terminal = { backend = "local"; timeout = 180; };
};

# personality.nix
services.curie-agent.settings = {
  display = { compact = false; personality = "kawaii"; };
  memory = { memory_enabled = true; user_profile_enabled = true; };
};
```

Both are deep-merged at evaluation time. Nix-declared keys always win over keys in an existing `config.yaml` on disk, but **user-added keys that Nix doesn't touch are preserved**. This means if the agent or a manual edit adds keys like `skills.disabled` or `streaming.enabled`, they survive `nixos-rebuild switch`.

:::note Model naming
`settings.model.default` uses the model identifier your provider expects. With [OpenRouter](https://openrouter.ai) (the default), these look like `"anthropic/claude-sonnet-4"` or `"google/gemini-3-flash"`. If you're using a provider directly (Anthropic, OpenAI), set `settings.model.base_url` to point at their API and use their native model IDs (e.g., `"claude-sonnet-4-20250514"`). When no `base_url` is set, Curie defaults to OpenRouter.
:::

:::tip Discovering available config keys
Run `nix build .#configKeys && cat result` to see every leaf config key extracted from Python's `DEFAULT_CONFIG`. You can paste your existing `config.yaml` into the `settings` attrset — the structure maps 1:1.
:::

<details>
<summary><strong>Full example: all commonly customized settings</strong></summary>

```nix
{ config, ... }: {
  services.curie-agent = {
    enable = true;
    container.enable = true;

    # ── Model ──────────────────────────────────────────────────────────
    settings = {
      model = {
        base_url = "https://openrouter.ai/api/v1";
        default = "anthropic/claude-opus-4.6";
      };
      toolsets = [ "all" ];
      max_turns = 100;
      terminal = { backend = "local"; cwd = "."; timeout = 180; };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "google/gemini-3-flash-preview";
      };
      memory = { memory_enabled = true; user_profile_enabled = true; };
      display = { compact = false; personality = "kawaii"; };
      agent = { max_turns = 60; verbose = false; };
    };

    # ── Secrets ────────────────────────────────────────────────────────
    environmentFiles = [ config.sops.secrets."curie-env".path ];

    # ── Documents ──────────────────────────────────────────────────────
    # USER.md is memory, so it goes to CURIE_HOME. Workspace files use
    # `documents`, and that option needs an explicit `workingDirectory`.
    curieHomeFiles = {
      "memories/USER.md" = ./documents/USER.md;
    };

    # ── MCP Servers ────────────────────────────────────────────────────
    mcpServers.filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };

    # ── Container options ──────────────────────────────────────────────
    container = {
      image = "ubuntu:24.04";
      backend = "docker";
      hostUsers = [ "your-username" ];
      extraVolumes = [ "/home/user/projects:/projects:rw" ];
      extraOptions = [ "--gpus" "all" ];
    };

    # ── Service tuning ─────────────────────────────────────────────────
    addToSystemPackages = true;
    extraArgs = [ "--verbose" ];
    restart = "always";
    restartSec = 5;
  };
}
```

</details>

### Escape Hatch: Bring Your Own Config

If you'd rather manage `config.yaml` entirely outside Nix, use `configFile`:

```nix
services.curie-agent.configFile = /etc/curie/config.yaml;
```

This bypasses `settings` entirely — no merge, no generation. The file is copied as-is to `$CURIE_HOME/config.yaml` on each activation.

### Customization Cheatsheet

Quick reference for the most common things Nix users want to customize:

| I want to... | Option | Example |
|---|---|---|
| Change the LLM model | `settings.model.default` | `"anthropic/claude-sonnet-4"` |
| Use a different provider endpoint | `settings.model.base_url` | `"https://openrouter.ai/api/v1"` |
| Add API keys | `environmentFiles` | `[ config.sops.secrets."curie-env".path ]` |
| Give the agent an identity | `curieHomeFiles."SOUL.md"` | `"You are a terse ops assistant."` |
| Add project context to the workspace | `documents."AGENTS.md"` | `./documents/AGENTS.md` |
| Run the backend for the desktop app or the dashboard | `backend.mode` | `"serve"` or `"dashboard"` |
| Add MCP tool servers | `mcpServers.<name>` | See [MCP Servers](#mcp-servers) |
| Enable Discord/Telegram/Slack | `extraDependencyGroups` | `[ "messaging" ]` |
| Mount host directories into container | `container.extraVolumes` | `[ "/data:/data:rw" ]` |
| Pass GPU access to container | `container.extraOptions` | `[ "--gpus" "all" ]` |
| Use Podman instead of Docker | `container.backend` | `"podman"` |
| Share state between host CLI and container | `container.hostUsers` | `[ "sidbin" ]` |
| Make extra tools available to the agent | `extraPackages` | `[ pkgs.pandoc pkgs.imagemagick ]` |
| Use a custom base image | `container.image` | `"ubuntu:24.04"` |
| Override the curie package | `package` | `inputs.curie-agent.packages.${system}.default.override { ... }` |
| Change state directory | `stateDir` | `"/opt/curie"` |
| Set the agent's working directory | `workingDirectory` | `"/home/user/projects"` |

---

## Secrets Management

:::danger Never put API keys in `settings` or `environment`
Values in Nix expressions end up in `/nix/store`, which is world-readable. Always use `environmentFiles` with a secrets manager.
:::

Both `environment` (non-secret vars) and `environmentFiles` (secret files) are merged into `$CURIE_HOME/.env` at activation time (`nixos-rebuild switch`). Curie reads this file on every startup, so changes take effect with a `systemctl restart curie-agent` — no container recreation needed.

### sops-nix

```nix
{
  sops = {
    defaultSopsFile = ./secrets/curie.yaml;
    age.keyFile = "/home/user/.config/sops/age/keys.txt";
    secrets."curie-env" = { format = "yaml"; };
  };

  services.curie-agent.environmentFiles = [
    config.sops.secrets."curie-env".path
  ];
}
```

The secrets file contains key-value pairs:

```yaml
# secrets/curie.yaml (encrypted with sops)
curie-env: |
    OPENROUTER_API_KEY=sk-or-...
    TELEGRAM_BOT_TOKEN=123456:ABC...
    ANTHROPIC_API_KEY=sk-ant-...
```

### agenix

```nix
{
  age.secrets.curie-env.file = ./secrets/curie-env.age;

  services.curie-agent.environmentFiles = [
    config.age.secrets.curie-env.path
  ];
}
```

### OAuth / Auth Seeding

For platforms requiring OAuth (e.g., Discord), use `authFile` to seed credentials on first deploy:

```nix
{
  services.curie-agent = {
    authFile = config.sops.secrets."curie/auth.json".path;
    # authFileForceOverwrite = true;  # overwrite on every activation
  };
}
```

The file is only copied if `auth.json` doesn't already exist (unless `authFileForceOverwrite = true`). Runtime OAuth token refreshes are written to the state directory and preserved across rebuilds.

---

## Documents

Curie reads files from two directories. Thus there are two options. Use the option for the directory that the file must go into.

`documents` installs into the **working directory** of the agent, which is `workingDirectory`. The agent reads its project context from that workspace:

```nix
{
  services.curie-agent = {
    # documents needs this option. Read the note below.
    workingDirectory = "/var/lib/curie/workspace";
    documents = {
      "AGENTS.md" = ./documents/AGENTS.md;   # path reference, copied from Nix store
      "notes/oncall.md" = "Page #infra before restarting anything.";
    };
  };
}
```

:::warning documents needs an explicit workingDirectory
The module refuses `documents` until you set `workingDirectory`. The default of
that option is different on each module. It is your home directory on Home
Manager, and `${stateDir}/workspace` on NixOS. Thus an unset default puts the
files in a directory that you did not select. A directory with the same path as
the default is a correct selection, and it satisfies the rule.
:::

`curieHomeFiles` installs into **`CURIE_HOME`**. Curie reads the identity file and the memory files of the agent from that directory. `SOUL.md` and `memories/` work only from there. A `SOUL.md` in `documents` makes a workspace file. Curie does not load that file as the identity:

```nix
{
  services.curie-agent.curieHomeFiles = {
    "SOUL.md" = "You are a helpful AI assistant.";
    "memories/USER.md" = ./documents/USER.md;
  };
}
```

Each value is a string or a path. A key in either option can contain subdirectories, and the module makes the parent directories. Each activation installs the files again.

`curieHomeFiles` needs no `workingDirectory`, because the module owns the `CURIE_HOME` directory. Most users want `curieHomeFiles`.

---

## MCP Servers

The `mcpServers` option declaratively configures [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers. Each server uses either **stdio** (local command) or **HTTP** (remote URL) transport.

### Stdio Transport (Local Servers)

```nix
{
  services.curie-agent.mcpServers = {
    filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };
    github = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-github" ];
      env.GITHUB_PERSONAL_ACCESS_TOKEN = "\${GITHUB_TOKEN}"; # resolved from .env
    };
  };
}
```

:::tip
Environment variables in `env` values are resolved from `$CURIE_HOME/.env` at runtime. Use `environmentFiles` to inject secrets — never put tokens directly in Nix config.
:::

### HTTP Transport (Remote Servers)

```nix
{
  services.curie-agent.mcpServers.remote-api = {
    url = "https://mcp.example.com/v1/mcp";
    headers.Authorization = "Bearer \${MCP_REMOTE_API_KEY}";
    timeout = 180;
  };
}
```

### HTTP Transport with OAuth

Set `auth = "oauth"` for servers using OAuth 2.1. Curie implements the full PKCE flow — metadata discovery, dynamic client registration, token exchange, and automatic refresh.

```nix
{
  services.curie-agent.mcpServers.my-oauth-server = {
    url = "https://mcp.example.com/mcp";
    auth = "oauth";
  };
}
```

Tokens are stored in `$CURIE_HOME/mcp-tokens/<server-name>.json` and persist across restarts and rebuilds.

<details>
<summary><strong>Initial OAuth authorization on headless servers</strong></summary>

The first OAuth authorization requires a browser-based consent flow. In a headless deployment, Curie prints the authorization URL to stdout/logs instead of opening a browser.

**Option A: Interactive bootstrap** — run the flow once via `docker exec` (container) or `sudo -u curie` (native):

```bash
# Container mode
docker exec -it curie-agent \
  curie mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth

# Native mode
sudo -u curie CURIE_HOME=/var/lib/curie/.curie \
  curie mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
```

The container uses `--network=host`, so the OAuth callback listener on `127.0.0.1` is reachable from the host browser.

**Option B: Pre-seed tokens** — complete the flow on a workstation, then copy tokens:

```bash
curie mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
scp ~/.curie/mcp-tokens/my-oauth-server{,.client}.json \
    server:/var/lib/curie/.curie/mcp-tokens/
# Ensure: chown curie:curie, chmod 0600
```

</details>

### Sampling (Server-Initiated LLM Requests)

Some MCP servers can request LLM completions from the agent:

```nix
{
  services.curie-agent.mcpServers.analysis = {
    command = "npx";
    args = [ "-y" "analysis-server" ];
    sampling = {
      enabled = true;
      model = "google/gemini-3-flash";
      max_tokens_cap = 4096;
      timeout = 30;
      max_rpm = 10;
    };
  };
}
```

---

## Managed Mode

When curie runs via the NixOS module, the following CLI commands are **blocked** with a descriptive error pointing you to `configuration.nix`:

| Blocked command | Why |
|---|---|
| `curie setup` | Config is declarative — edit `settings` in your Nix config |
| `curie config edit` | Config is generated from `settings` |
| `curie config set <key> <value>` | Config is generated from `settings` |
| `curie gateway install` | The systemd service is managed by NixOS |
| `curie gateway uninstall` | The systemd service is managed by NixOS |

This prevents drift between what Nix declares and what's on disk. Detection uses two signals:

1. **The `CURIE_MANAGED` environment variable.** The service sets it, and the gateway process reads it.
2. **The `.managed` marker file** in `CURIE_HOME`. The activation script writes it, and an interactive shell reads it. Thus the CLI also blocks a command such as `docker exec -it curie-agent curie config set ...`.

Both signals hold the name of the system that manages the install. Thus the refusal names the correct rebuild command. The NixOS module gives `sudo nixos-rebuild switch`. The Home Manager module gives `home-manager switch`.

---

## Home Manager Module

The flake also exports `homeManagerModules.default`. Curie is an agent for one person. The credentials, the memory, the sessions and the cron jobs all belong to that person. Thus a user service is the correct shape on a personal machine. It runs on each distribution that Home Manager supports, and not only on NixOS.

The option set is the same set that the NixOS module uses. It is `services.curie-agent`, with the same `settings`, `environmentFiles`, `documents`, `mcpServers`, `extraPlugins` and `backend` options. Each example above works here without a change. Only the necessary parts are different:

| | NixOS module | Home Manager module |
|---|---|---|
| Runs as | a system user that you declare, with `user`, `group` and `createUser` | you |
| State directory | `stateDir` and `/.curie` | `curieHome`, set directly. The default is `~/.curie`. |
| Service | `systemd.services` | `systemd.user.services` on Linux, `launchd.agents` on macOS |
| CLI on the PATH | `addToSystemPackages`, which exports `CURIE_HOME` for the full system | `programs.curie-agent.enable`, which exports it for your session only |
| Desktop application | not supported, because a system service cannot own a user session | `programs.curie-agent.desktop.enable` |
| Container mode | supported | not supported, because it needs root and the Docker socket |

### Add the Flake Input

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
    curie-agent.url = "github:thisismynewfmail-ui/Cur-Agnt";
  };
}
```

Then import the module into your Home Manager configuration. The configuration can be standalone. It can also be under `home-manager.users.<name>` in a NixOS or nix-darwin configuration:

```nix
{
  imports = [ curie-agent.homeManagerModules.default ];

  services.curie-agent = {
    enable = true;
    gateway.enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."curie-env".path ];
  };
}
```

`home-manager switch` makes `~/.curie`, writes `config.yaml`, builds `.env` and starts the gateway as a user service.

:::warning Enable linger, or the service stops at logout
CAUTION: Enable linger for your account. Without linger, systemd stops the user manager when your last session ends, and the gateway stops with it. Home Manager cannot set linger, because linger is a property of the account:

```nix
# NixOS
users.users.your-username.linger = true;
```

```bash
# anywhere else
sudo loginctl enable-linger your-username
```

macOS has no equivalent option. A `launchd` agent with `RunAtLoad` starts at login and continues to run.
:::

### Running the Desktop / Dashboard Backend

`gateway.enable` runs the messaging gateway for Telegram, Discord, Slack and the other platforms. Curie Desktop and the web dashboard connect to a *different* process, which is `curie serve` or `curie dashboard`. `backend.mode` runs that process with the gateway:

```nix
{
  services.curie-agent = {
    enable = true;
    gateway.enable = true;      # messaging platforms
    backend.mode = "dashboard"; # + the browser dashboard on 127.0.0.1:9119
    backend.port = 9119;
  };
}
```

`serve` runs without a user interface. It gives the `/api/ws` and `/api/pty` sockets that Curie Desktop connects to, and it does not build the web application. `dashboard` gives all of that, and also serves the browser admin panel. Both processes use one `CURIE_HOME` with the gateway. Thus the sessions, the skills, the memory and the cron jobs are the same for all of them. `backend.mode` works in the same way on the NixOS module, but not in container mode.

:::warning Binding to an address other than loopback
The default address is `127.0.0.1`. Each other address starts the authentication gate of the dashboard. The server also refuses each request with a `Host` header that is different from the address that the server bound to. This is a defence against DNS rebinding. Bind to the name or the address that your client uses.
:::

### Verify It Works

```bash
# Linux
systemctl --user status curie-agent
journalctl --user -u curie-agent -f

# macOS
launchctl list | grep curie
tail -f ~/Library/Logs/curie-agent.log

curie --version
curie config     # shows the configuration that Nix wrote
```

---

## Container Architecture

:::info
This section is only relevant if you're using `container.enable = true`. Skip it for native mode deployments.
:::

When container mode is enabled, curie runs inside a persistent Ubuntu container with the Nix-built binary bind-mounted read-only from the host:

```
Host                                    Container
────                                    ─────────
/nix/store/...-curie-agent-0.1.0  ──►  /nix/store/... (ro)
~/.curie -> /var/lib/curie/.curie       (symlink bridge, per hostUsers)
/var/lib/curie/                    ──►  /data/          (rw)
  ├── current-package -> /nix/store/...    (symlink, updated each rebuild)
  ├── .gc-root -> /nix/store/...           (prevents nix-collect-garbage)
  ├── .container-identity                  (sha256 hash, triggers recreation)
  ├── .curie/                             (CURIE_HOME)
  │   ├── .env                             (merged from environment + environmentFiles)
  │   ├── config.yaml                      (Nix-generated, deep-merged by activation)
  │   ├── .managed                         (marker file)
  │   ├── .container-mode                  (routing metadata: backend, exec_user, etc.)
  │   ├── state.db, sessions/, memories/   (runtime state)
  │   └── mcp-tokens/                      (OAuth tokens for MCP servers)
  ├── home/                                ──►  /home/curie    (rw)
  └── workspace/                           (agent working directory)
      ├── AGENTS.md                        (from the documents option)
      └── (agent-created files)

Container writable layer (apt/pip/npm):   /usr, /usr/local, /tmp
```

The Nix-built binary works inside the Ubuntu container because `/nix/store` is bind-mounted — it brings its own interpreter and all dependencies, so there's no reliance on the container's system libraries. The container entrypoint resolves through a `current-package` symlink: `/data/current-package/bin/curie gateway run --replace`. On `nixos-rebuild switch`, only the symlink is updated — the container keeps running.

### What Persists Across What

| Event | Container recreated? | `/data` (state) | `/home/curie` | Writable layer (`apt`/`pip`/`npm`) |
|---|---|---|---|---|
| `systemctl restart curie-agent` | No | Persists | Persists | Persists |
| `nixos-rebuild switch` (code change) | No (symlink updated) | Persists | Persists | Persists |
| Host reboot | No | Persists | Persists | Persists |
| `nix-collect-garbage` | No (GC root) | Persists | Persists | Persists |
| Image change (`container.image`) | **Yes** | Persists | Persists | **Lost** |
| Volume/options change | **Yes** | Persists | Persists | **Lost** |
| `environment`/`environmentFiles` change | No | Persists | Persists | Persists |

The container is only recreated when its **identity hash** changes. The hash covers: schema version, image, `extraVolumes`, `extraOptions`, and the entrypoint script. Changes to environment variables, settings, documents, or the curie package itself do **not** trigger recreation.

:::warning Writable layer loss
When the identity hash changes (image upgrade, new volumes, new container options), the container is destroyed and recreated from a fresh pull of `container.image`. Any `apt install`, `pip install`, or `npm install` packages in the writable layer are lost. State in `/data` and `/home/curie` is preserved (these are bind mounts).

If the agent relies on specific packages, consider baking them into a custom image (`container.image = "my-registry/curie-base:latest"`) or scripting their installation in the agent's SOUL.md.
:::

### GC Root Protection

The `preStart` script creates a GC root at `${stateDir}/.gc-root` pointing to the current curie package. This prevents `nix-collect-garbage` from removing the running binary. If the GC root somehow breaks, restarting the service recreates it.

---

## Plugins

The NixOS module supports declarative plugin installation — no imperative `curie plugins install` needed.

### Directory Plugins (`extraPlugins`)

For plugins that are just a source tree with `plugin.yaml` + `__init__.py` (e.g., [curie-lcm](https://github.com/stephenschoettler/curie-lcm)):

```nix
services.curie-agent.extraPlugins = [
  (pkgs.fetchFromGitHub {
    owner = "stephenschoettler";
    repo = "curie-lcm";
    rev = "v0.7.0";
    hash = "sha256-...";
  })
];
```

Plugins are symlinked into `$CURIE_HOME/plugins/` at activation time. Curie discovers them via its normal directory scan. Removing a plugin from the list and running `nixos-rebuild switch` removes the symlink.

### Entry-Point Plugins (`extraPythonPackages`)

For pip-packaged plugins that register via `[project.entry-points."curie_agent.plugins"]` (e.g., [rtk-curie](https://github.com/ogallotti/rtk-curie)):

```nix
services.curie-agent.extraPythonPackages = [
  (pkgs.python312Packages.buildPythonPackage {
    pname = "rtk-curie";
    version = "1.0.0";
    src = pkgs.fetchFromGitHub {
      owner = "ogallotti";
      repo = "rtk-curie";
      rev = "v1.0.0";
      hash = "sha256-...";
    };
    format = "pyproject";
    build-system = [ pkgs.python312Packages.setuptools ];
  })
];
```

The package's `site-packages` is added to PYTHONPATH in the curie wrapper. `importlib.metadata` discovers the entry point at session start.

### Optional Dependency Groups (`extraDependencyGroups`)

For optional extras declared in curie-agent's `pyproject.toml`, use `extraDependencyGroups` to include them in the sealed venv at build time. This is required for any extra not in the default `[all]` set — on Nix, runtime installation into the read-only store is not possible.

```nix
# Enable Discord, Telegram, Slack
services.curie-agent.extraDependencyGroups = [ "messaging" ];
```

```nix
# Enable a memory provider
services.curie-agent = {
  extraDependencyGroups = [ "hindsight" ];
  settings.memory.provider = "hindsight";
};
```

This is resolved by uv alongside core dependencies — no PYTHONPATH patching, no collision risk. Available groups:

| Group | What it enables |
|-------|-----------------|
| `messaging` | Discord, Telegram, Slack |
| `matrix` | Matrix/Element (mautrix with encryption; Linux only) |
| `dingtalk` | DingTalk |
| `feishu` | Feishu/Lark |
| `voice` | Local speech-to-text (faster-whisper) |
| `edge-tts` | Edge TTS provider |
| `tts-premium` | ElevenLabs TTS |
| `anthropic` | Native Anthropic SDK (not needed via OpenRouter) |
| `bedrock` | AWS Bedrock (boto3) |
| `azure-identity` | Azure Entra ID auth |
| `honcho` | Honcho memory provider |
| `hindsight` | Hindsight memory provider |
| `modal` | Modal terminal backend |
| `daytona` | Daytona terminal backend |
| `exa` | Exa web search |
| `firecrawl` | Firecrawl web search |
| `fal` | FAL image generation |

Or use the pre-built `#messaging` or `#full` flake packages instead of per-extra configuration (see [Quick Start](#quick-start-any-nix-user)).

**When to use which:**

| Need | Option |
|------|--------|
| Enable a pyproject.toml optional extra | `extraDependencyGroups` |
| Add an external Python plugin not in pyproject.toml | `extraPythonPackages` |
| Add a system binary (pandoc, jq, etc.) | `extraPackages` |
| Add a directory-based plugin source tree | `extraPlugins` |

### Combining Both

A directory plugin with third-party Python dependencies needs both options:

```nix
services.curie-agent = {
  extraPlugins = [ my-plugin-src ];          # plugin source
  extraPythonPackages = [ pkgs.python312Packages.redis ];  # its Python dep
  extraPackages = [ pkgs.redis ];            # system binary it needs
};
```

### Using the Overlay

External flakes can override the package directly:

```nix
{
  inputs.curie-agent.url = "github:thisismynewfmail-ui/Cur-Agnt";
  outputs = { curie-agent, nixpkgs, ... }: {
    nixpkgs.overlays = [ curie-agent.overlays.default ];
    # Then:
    #   pkgs.curie-agent.override { extraPythonPackages = [...]; }
    #   pkgs.curie-agent.override { extraDependencyGroups = [ "hindsight" ]; }
  };
}
```

### Plugin Configuration

Plugins still need to be enabled in `config.yaml`. Add them via the declarative settings:

```nix
services.curie-agent.settings.plugins.enabled = [
  "curie-lcm"
  "rtk-rewrite"
];
```

:::note
A build-time collision check prevents plugin packages from shadowing core curie dependencies. If a plugin provides a package already in the sealed venv, `nixos-rebuild` fails with a clear error.
:::

---

## Development

### Dev Shell

The flake provides a development shell with Python 3.12, uv, Node.js, and all runtime tools:

```bash
cd curie-agent
nix develop

# Shell provides:
#   - Python 3.12 + uv (deps installed into .venv on first entry)
#   - Node.js 26, ripgrep, git, openssh, ffmpeg on PATH
#   - Stamp-file optimization: re-entry is near-instant if deps haven't changed

curie setup
curie chat
```

### direnv (Recommended)

The included `.envrc` activates the dev shell automatically:

```bash
cd curie-agent
direnv allow    # one-time
# Subsequent entries are near-instant (stamp file skips dep install)
```

### Flake Checks

The flake includes build-time verification that runs in CI and locally:

```bash
# Run all checks
nix flake check

# Individual checks
nix build .#checks.x86_64-linux.package-contents   # binaries exist + version
nix build .#checks.x86_64-linux.entry-points-sync  # pyproject.toml ↔ Nix package sync
nix build .#checks.x86_64-linux.cli-commands        # gateway/config subcommands
nix build .#checks.x86_64-linux.managed-guard       # CURIE_MANAGED blocks mutation
nix build .#checks.x86_64-linux.bundled-skills      # skills present in package
nix build .#checks.x86_64-linux.config-roundtrip    # merge script preserves user keys
```

<details>
<summary><strong>What each check verifies</strong></summary>

| Check | What it tests |
|---|---|
| `package-contents` | `curie` and `curie-agent` binaries exist and `curie --version` runs |
| `entry-points-sync` | Every `[project.scripts]` entry in `pyproject.toml` has a wrapped binary in the Nix package |
| `cli-commands` | `curie --help` exposes `gateway` and `config` subcommands |
| `managed-guard` | `CURIE_MANAGED=true curie config set ...` prints the NixOS error |
| `bundled-skills` | Skills directory exists, contains SKILL.md files, `CURIE_BUNDLED_SKILLS` is set in wrapper |
| `config-roundtrip` | 7 merge scenarios: fresh install, Nix override, user key preservation, mixed merge, MCP additive merge, nested deep merge, idempotency |

</details>

---

## Options Reference

### Core

| Option | Type | Default | Description |
|---|---|---|---|
| `enable` | `bool` | `false` | Enable the curie-agent service |
| `package` | `package` | `curie-agent` | The curie-agent package to use |
| `user` | `str` | `"curie"` | System user |
| `group` | `str` | `"curie"` | System group |
| `createUser` | `bool` | `true` | Auto-create user/group |
| `stateDir` | `str` | `"/var/lib/curie"` | State directory (`CURIE_HOME` parent) |
| `workingDirectory` | `str` | `"${stateDir}/workspace"` | Agent working directory |
| `addToSystemPackages` | `bool` | `false` | Add `curie` CLI to system PATH and set `CURIE_HOME` system-wide |

### Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `settings` | `attrs` (deep-merged) | `{}` | Declarative config rendered as `config.yaml`. Supports arbitrary nesting; multiple definitions are merged via `lib.recursiveUpdate` |
| `configFile` | `null` or `path` | `null` | Path to an existing `config.yaml`. Overrides `settings` entirely if set |

### Secrets & Environment

| Option | Type | Default | Description |
|---|---|---|---|
| `environmentFiles` | `listOf str` | `[]` | Paths to env files with secrets. Merged into `$CURIE_HOME/.env` at activation time |
| `environment` | `attrsOf str` | `{}` | Non-secret env vars. **Visible in Nix store** — do not put secrets here |
| `authFile` | `null` or `path` | `null` | OAuth credentials seed. Only copied on first deploy |
| `authFileForceOverwrite` | `bool` | `false` | Always overwrite `auth.json` from `authFile` on activation |

### Documents

| Option | Type | Default | Description |
|---|---|---|---|
| `documents` | `attrsOf (either str path)` | `{}` | Workspace files. Each key is a path relative to `workingDirectory`. You must set that option to use this one. |
| `curieHomeFiles` | `attrsOf (either str path)` | `{}` | Files that go into `CURIE_HOME`. `SOUL.md` and `memories/` must be here, or Curie does not load them. |

### MCP Servers

| Option | Type | Default | Description |
|---|---|---|---|
| `mcpServers` | `attrsOf submodule` | `{}` | MCP server definitions, merged into `settings.mcp_servers` |
| `mcpServers.<name>.command` | `null` or `str` | `null` | Server command (stdio transport) |
| `mcpServers.<name>.args` | `listOf str` | `[]` | Command arguments |
| `mcpServers.<name>.env` | `attrsOf str` | `{}` | Environment variables for the server process |
| `mcpServers.<name>.url` | `null` or `str` | `null` | Server endpoint URL (HTTP/StreamableHTTP transport) |
| `mcpServers.<name>.headers` | `attrsOf str` | `{}` | HTTP headers, e.g. `Authorization` |
| `mcpServers.<name>.auth` | `null` or `"oauth"` | `null` | Authentication method. `"oauth"` enables OAuth 2.1 PKCE |
| `mcpServers.<name>.enabled` | `bool` | `true` | Enable or disable this server |
| `mcpServers.<name>.timeout` | `null` or `int` | `null` | Tool call timeout in seconds (default: 120) |
| `mcpServers.<name>.connect_timeout` | `null` or `int` | `null` | Connection timeout in seconds (default: 60) |
| `mcpServers.<name>.tools` | `null` or `submodule` | `null` | Tool filtering (`include`/`exclude` lists) |
| `mcpServers.<name>.sampling` | `null` or `submodule` | `null` | Sampling config for server-initiated LLM requests |

### Service Behavior

| Option | Type | Default | Description |
|---|---|---|---|
| `extraArgs` | `listOf str` | `[]` | Extra args for `curie gateway` |
| `extraPackages` | `listOf package` | `[]` | Extra packages available to the agent. Added to the curie user's per-user profile so terminal commands, skills, and cron jobs all see them |
| `extraPlugins` | `listOf package` | `[]` | Directory plugin packages to symlink into `$CURIE_HOME/plugins/`. Each must contain `plugin.yaml` |
| `extraPythonPackages` | `listOf package` | `[]` | Python packages added to PYTHONPATH for entry-point plugin discovery. Build with `python312Packages` |
| `extraDependencyGroups` | `listOf str` | `[]` | pyproject.toml optional extras to include in the sealed venv (e.g. `["hindsight"]`). Resolved by uv — no collisions |
| `restart` | `str` | `"always"` | The systemd `Restart=` policy. macOS does not use it. |
| `restartSec` | `int` | `5` | The systemd `RestartSec=` value. macOS does not use it. |

### Backend (`curie serve` / `curie dashboard`)

This option runs the process that Curie Desktop and the web dashboard connect to, with the gateway. You cannot use it with `container.enable`.

| Option | Type | Default | Description |
|---|---|---|---|
| `backend.mode` | `enum ["none" "serve" "dashboard"]` | `"none"` | `serve` runs without a user interface and gives `/api/ws` and `/api/pty`. `dashboard` also serves the browser panel. |
| `backend.host` | `str` | `"127.0.0.1"` | The address to bind to. Each address other than loopback starts the authentication gate. |
| `backend.port` | `port` | `9119` | The port to bind to |
| `backend.extraArgs` | `listOf str` | `[]` | More arguments for the backend command |

### Home Manager only

| Option | Type | Default | Description |
|---|---|---|---|
| `curieHome` | `str` | `"${config.home.homeDirectory}/.curie"` | `CURIE_HOME` directly. The NixOS module builds it from `stateDir`. |
| `gateway.enable` | `bool` | `false` | Run the messaging gateway. On the NixOS module the gateway is the service, so that module has no such option. |

### `programs.curie-agent` (Home Manager only)

Home Manager separates "install this application for me" from "run this
daemon". `services.curie-agent` keeps the state, the configuration and the
daemons. `programs.curie-agent` installs what you use, and reads
`curieHome` and the backend address from the services.

| Option | Type | Default | Description |
|---|---|---|---|
| `enable` | `bool` | `false` | Add the `curie` CLI to `home.packages`, and export `CURIE_HOME` for your shells |
| `package` | `package` | `services.curie-agent.package` | The package to install. The default applies `extraPythonPackages` and `extraDependencyGroups` from the services, so both are one build. |
| `desktop.enable` | `bool` | `false` | Add the Curie Desktop application, with a launcher entry on Linux |
| `desktop.package` | `package` | `package.curieDesktop` | The desktop package. The default follows `package`, so the application and the services run one Curie runtime. |

```nix
programs.curie-agent = {
  enable = true;
  desktop.enable = true;
};

services.curie-agent = {
  enable = true;
  backend.mode = "serve";
  backend.sessionTokenFile = config.sops.secrets."curie/desktop-token".path;
};
```

The launcher carries `CURIE_HOME` itself. A desktop menu reads no shell
profile, so the value that `programs.curie-agent.enable` exports with
`home.sessionVariables` reaches an interactive shell only. Without the
value in the launcher, the application opens `~/.curie` while the
services use `curieHome`, and you see no sessions and no keys.

With `backend.sessionTokenFile`, the application connects to the backend
of the service instead of starting one of its own. Both sides read the
file at start time, so the token enters no Nix store path. Without the
option, each side runs its own backend.

`services.curie-agent.installPackage` was removed by this split. A
configuration that still sets it gets an error that names the
replacement.

### Container (NixOS only)

| Option | Type | Default | Description |
|---|---|---|---|
| `container.enable` | `bool` | `false` | Enable OCI container mode |
| `container.backend` | `enum ["docker" "podman"]` | `"docker"` | Container runtime |
| `container.image` | `str` | `"ubuntu:24.04"` | Base image (pulled at runtime) |
| `container.extraVolumes` | `listOf str` | `[]` | Extra volume mounts (`host:container:mode`) |
| `container.extraOptions` | `listOf str` | `[]` | Extra args passed to `docker create` |
| `container.hostUsers` | `listOf str` | `[]` | Interactive users who get a `~/.curie` symlink to the service stateDir and are auto-added to the `curie` group |

---

## Directory Layout

### Native Mode

```
/var/lib/curie/                     # stateDir (owned by curie:curie, 0750)
├── .curie/                         # CURIE_HOME
│   ├── SOUL.md                      # from curieHomeFiles: the agent identity
│   ├── config.yaml                  # Nix-generated (deep-merged each rebuild)
│   ├── .managed                     # Marker: CLI config mutation blocked
│   ├── .env                         # Merged from environment + environmentFiles
│   ├── auth.json                    # OAuth credentials (seeded, then self-managed)
│   ├── gateway.pid
│   ├── state.db
│   ├── mcp-tokens/                  # OAuth tokens for MCP servers
│   ├── sessions/
│   ├── memories/
│   ├── skills/
│   ├── cron/
│   └── logs/
├── home/                            # Agent HOME
└── workspace/                       # Agent working directory
    ├── AGENTS.md                    # from the documents option
    └── (agent-created files)
```

### Home Manager

```
~/.curie/                           # curieHome (CURIE_HOME), 0700
├── SOUL.md                          # from curieHomeFiles
├── config.yaml                      # written by Nix, merged at each activation
├── .managed                         # marker: names the system that manages this
├── .env                             # written again from environment + environmentFiles
├── auth.json                        # OAuth credentials: seeded, then Curie owns it
├── memories/  sessions/  skills/  cron/  logs/  plugins/
└── (runtime state)

~/                                   # workingDirectory, your home by default
└── AGENTS.md                        # from the documents option
```

### Container Mode

Same layout, mounted into the container:

| Container path | Host path | Mode | Notes |
|---|---|---|---|
| `/nix/store` | `/nix/store` | `ro` | Curie binary + all Nix deps |
| `/data` | `/var/lib/curie` | `rw` | All state, config, workspace |
| `/home/curie` | `${stateDir}/home` | `rw` | Persistent agent home — `pip install --user`, tool caches |
| `/usr`, `/usr/local`, `/tmp` | (writable layer) | `rw` | `apt`/`pip`/`npm` installs — persists across restarts, lost on recreation |

---

## Updating

```bash
# Update the flake input (run from the directory containing flake.nix)
cd /etc/nixos && nix flake update curie-agent

# Rebuild
sudo nixos-rebuild switch          # for the NixOS module
home-manager switch                # for the Home Manager module
```

In container mode, the `current-package` symlink is updated and the agent picks up the new binary on restart. No container recreation, no loss of installed packages.

---

## Troubleshooting

:::tip Podman users
All `docker` commands below work the same with `podman`. Substitute accordingly if you set `container.backend = "podman"`.
:::

### Service Logs

```bash
# Both modes use the same systemd unit
journalctl -u curie-agent -f

# Container mode: also available directly
docker logs -f curie-agent
```

### Container Inspection

```bash
systemctl status curie-agent
docker ps -a --filter name=curie-agent
docker inspect curie-agent --format='{{.State.Status}}'
docker exec -it curie-agent bash
docker exec curie-agent readlink /data/current-package
docker exec curie-agent cat /data/.container-identity
```

### Force Container Recreation

If you need to reset the writable layer (fresh Ubuntu):

```bash
sudo systemctl stop curie-agent
docker rm -f curie-agent
sudo rm /var/lib/curie/.container-identity
sudo systemctl start curie-agent
```

### Verify Secrets Are Loaded

If the agent starts but can't authenticate with the LLM provider, check that the `.env` file was merged correctly:

```bash
# Native mode
sudo -u curie cat /var/lib/curie/.curie/.env

# Container mode
docker exec curie-agent cat /data/.curie/.env
```

### GC Root Verification

```bash
nix-store --query --roots $(docker exec curie-agent readlink /data/current-package)
```

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot save configuration: managed by NixOS` | CLI guards active | Edit `configuration.nix` and `nixos-rebuild switch` |
| `No adapter available for discord` (or telegram/slack) | Messaging deps missing from the sealed Nix venv | Install `#messaging` variant: `nix profile install ...#messaging`. For NixOS module: `extraDependencyGroups = [ "messaging" ]`. Check `journalctl -u curie-agent` for `FeatureUnavailable` or `requirements not met` for the underlying error. |
| Container recreated unexpectedly | `extraVolumes`, `extraOptions`, or `image` changed | Expected — writable layer resets. Reinstall packages or use a custom image |
| `curie --version` shows old version | Container not restarted | `systemctl restart curie-agent` |
| Permission denied on `/var/lib/curie` | State dir is `0750 curie:curie` | Use `docker exec` or `sudo -u curie` |
| `nix-collect-garbage` removed curie | GC root missing | Restart the service (preStart recreates the GC root) |
| `no container with name or ID "curie-agent"` (Podman) | Podman rootful container not visible to regular user | Add passwordless sudo for podman (see [Container Mode](#container-mode) section) |
| `unable to find user curie` | Container still starting (entrypoint hasn't created user yet) | Wait a few seconds and retry — the CLI retries automatically |
| Tool added via `extraPackages` not found in terminal | Requires `nixos-rebuild switch` to update the per-user profile | Rebuild and restart: `nixos-rebuild switch && systemctl restart curie-agent` |
