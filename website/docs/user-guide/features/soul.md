---
sidebar_position: 9
title: "SOUL.md — the agent's voice"
description: "SOUL.md is the single description of who Curie Agent is. Edit it and the next reply reflects it."
---

# SOUL.md — the agent's voice

`SOUL.md` is the **only** description of who the agent is. It occupies slot #1
in the system prompt, it is re-read on every message, and editing it changes
the next reply — no restart, no config round-trip.

If you want to change how Curie writes, or replace it with an entirely
different agent, edit this one file.

:::info There used to be two
Earlier versions also carried a `/personality` command backed by a table of
built-in character sheets ("kawaii", "pirate", "noir", …) selected by
`display.personality` and extensible through `agent.personalities`. Two
independent sources of voice is one too many: whichever won, the other was
silently ignored, and the config side kept resurrecting itself out of stale
per-surface state.

The table, both config keys, and `/personality` on every surface are gone.
The first run after upgrading strips the dead keys and tells you what it
removed. `agent.system_prompt` — the manual override you write yourself — is
untouched and still works.
:::

## How SOUL.md works now

Curie now seeds a default `SOUL.md` automatically in:

```text
~/.curie/SOUL.md
```

More precisely, it uses the current instance's `CURIE_HOME`, so if you run Curie with a custom home directory, it will use:

```text
$CURIE_HOME/SOUL.md
```

### Important behavior

- **SOUL.md is the agent's primary identity.** It occupies slot #1 in the system prompt, replacing the hardcoded default identity.
- Curie creates a starter `SOUL.md` automatically if one does not exist yet
- Existing user `SOUL.md` files are never overwritten
- Curie loads `SOUL.md` only from `CURIE_HOME`
- Curie does not look in the current working directory for `SOUL.md`
- If `SOUL.md` exists but is empty, or cannot be loaded, Curie falls back to a built-in default identity
- If `SOUL.md` has content, that content is injected verbatim after security scanning and truncation
- SOUL.md is **not** duplicated in the context files section — it appears only once, as the identity

That makes `SOUL.md` a true per-user or per-instance identity, not just an additive layer.

## Why this design

This keeps personality predictable.

If Curie loaded `SOUL.md` from whatever directory you happened to launch it in, your personality could change unexpectedly between projects. By loading only from `CURIE_HOME`, the personality belongs to the Curie instance itself.

That also makes it easier to teach users:
- "Edit `~/.curie/SOUL.md` to change Curie' default personality."

## Where to edit it

For most users:

```bash
~/.curie/SOUL.md
```

If you use a custom home:

```bash
$CURIE_HOME/SOUL.md
```

## What should go in SOUL.md?

Use it for durable voice and personality guidance, such as:
- tone
- communication style
- level of directness
- default interaction style
- what to avoid stylistically
- how Curie should handle uncertainty, disagreement, or ambiguity

Use it less for:
- one-off project instructions
- file paths
- repo conventions
- temporary workflow details

Those belong in `AGENTS.md`, not `SOUL.md`.

## Good SOUL.md content

A good SOUL file is:
- stable across contexts
- broad enough to apply in many conversations
- specific enough to materially shape the voice
- focused on communication and identity, not task-specific instructions

### Example

```markdown
# Personality

You are a pragmatic senior engineer with strong taste.
You optimize for truth, clarity, and usefulness over politeness theater.

## Style
- Be direct without being cold
- Prefer substance over filler
- Push back when something is a bad idea
- Admit uncertainty plainly
- Keep explanations compact unless depth is useful

## What to avoid
- Sycophancy
- Hype language
- Repeating the user's framing if it's wrong
- Overexplaining obvious things

## Technical posture
- Prefer simple systems over clever systems
- Care about operational reality, not idealized architecture
- Treat edge cases as part of the design, not cleanup
```

## What Curie injects into the prompt

`SOUL.md` content goes directly into slot #1 of the system prompt — the agent identity position. No wrapper language is added around it.

The content goes through:
- prompt-injection scanning
- truncation if it is too large

If the file is empty, whitespace-only, or cannot be read, Curie falls back to a built-in default identity ("You are Curie Agent, built by Nous Research. Be direct: match the length of your reply to the weight of the ask..."). This fallback also applies when `skip_context_files` is set (e.g., in subagent/delegation contexts).

## Security scanning

`SOUL.md` is scanned like other context-bearing files for prompt injection patterns before inclusion.

That means you should still keep it focused on persona/voice rather than trying to sneak in strange meta-instructions.

## SOUL.md vs AGENTS.md

This is the most important distinction.

### SOUL.md
Use for:
- identity
- tone
- style
- communication defaults
- personality-level behavior

### AGENTS.md
Use for:
- project architecture
- coding conventions
- tool preferences
- repo-specific workflows
- commands, ports, paths, deployment notes

A useful rule:
- if it should follow you everywhere, it belongs in `SOUL.md`
- if it belongs to a project, it belongs in `AGENTS.md`

## The manual override

`agent.system_prompt` in `~/.curie/config.yaml` is appended to the prompt on
top of `SOUL.md`. It is not a personality: it ships with no presets, nothing
in the codebase writes it, and it exists for the case where you want a
standing instruction that is awkward to phrase as identity.

```yaml
agent:
  system_prompt: >
    Answer in metric units. Prefer SI symbols over spelled-out names.
```

`CURIE_EPHEMERAL_SYSTEM_PROMPT` does the same thing for one process, and takes
precedence over the config value. Use it for a one-off run:

```bash
CURIE_EPHEMERAL_SYSTEM_PROMPT="Reply in Spanish for this session." curie chat
```

Most setups need neither. Prefer writing what you want into `SOUL.md`, where
it is one file you can read.

## Resetting to the default

Delete the file, or empty it, and Curie falls back to its built-in identity:

```bash
rm ~/.curie/SOUL.md      # regenerated with the default on next run
```

To go back to the shipped default without losing your own version, move it
aside first:

```bash
mv ~/.curie/SOUL.md ~/.curie/SOUL.md.bak
```

## Recommended workflow

1. Write a considered `SOUL.md` in `~/.curie/SOUL.md` — voice, length, what to
   avoid.
2. Put project-specific instructions in that project's `AGENTS.md`, not here.
3. Reach for `agent.system_prompt` only when something has to sit outside the
   agent's identity.

That gives a stable voice, project behaviour where it belongs, and one file to
edit when you want the agent to sound different.

## How SOUL.md sits in the full prompt

1. **SOUL.md** — agent identity, or the built-in fallback when it is empty
2. tool-aware behaviour guidance
3. memory / user context
4. skills guidance
5. context files (`AGENTS.md`, `.cursorrules`)
6. timestamp
7. platform-specific formatting hints
8. `agent.system_prompt` / `CURIE_EPHEMERAL_SYSTEM_PROMPT`, when set

`SOUL.md` is the foundation — everything else builds on top of it.

## Voice is not appearance

How Curie *writes* and how Curie *looks* are separate settings:

- `SOUL.md` and `agent.system_prompt` affect how it writes
- `display.skin` and `/skin` affect how it looks in the terminal

For terminal appearance, see [Skins & Themes](./skins.md).

## Related docs

- [Context Files](/user-guide/features/context-files)
- [Configuration](/user-guide/configuration)
- [Tips & Best Practices](/guides/tips)
- [SOUL.md Guide](/guides/use-soul-with-curie)
- [Skins & Themes](./skins.md)
