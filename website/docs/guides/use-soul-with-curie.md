---
sidebar_position: 7
title: "Use SOUL.md with Curie"
description: "How to use SOUL.md to shape Curie Agent's voice, what belongs there, and how it differs from AGENTS.md"
---

# Use SOUL.md with Curie

`SOUL.md` is the **primary identity** for your Curie instance. It's the first thing in the system prompt — it defines who the agent is, how it speaks, and what it avoids.

If you want Curie to feel like the same assistant every time you talk to it — or if you want to replace the Curie persona entirely with your own — this is the file to use.

## What SOUL.md is for

Use `SOUL.md` for:
- tone
- personality
- communication style
- how direct or warm Curie should be
- what Curie should avoid stylistically
- how Curie should relate to uncertainty, disagreement, and ambiguity

In short:
- `SOUL.md` is about who Curie is and how Curie speaks

## What SOUL.md is not for

Do not use it for:
- repo-specific coding conventions
- file paths
- commands
- service ports
- architecture notes
- project workflow instructions

Those belong in `AGENTS.md`.

A good rule:
- if it should apply everywhere, put it in `SOUL.md`
- if it only belongs to one project, put it in `AGENTS.md`

## Where it lives

Curie now uses only the global SOUL file for the current instance:

```text
~/.curie/SOUL.md
```

If you run Curie with a custom home directory, it becomes:

```text
$CURIE_HOME/SOUL.md
```

## First-run behavior

Curie automatically seeds a starter `SOUL.md` for you if one does not already exist.

That means most users now begin with a real file they can read and edit immediately.

Important:
- if you already have a `SOUL.md`, Curie does not overwrite it
- if the file exists but is empty, Curie adds nothing from it to the prompt

## How Curie uses it

When Curie starts a session, it reads `SOUL.md` from `CURIE_HOME`, scans it for prompt-injection patterns, truncates it if needed, and uses it as the **agent identity** — slot #1 in the system prompt. This means SOUL.md completely replaces the built-in default identity text.

If SOUL.md is missing, empty, or cannot be loaded, Curie falls back to a built-in default identity.

No wrapper language is added around the file. The content itself matters — write the way you want your agent to think and speak.

## A good first edit

If you do nothing else, open the file and change just a few lines so it feels like you.

For example:

```markdown
You are direct, calm, and technically precise.
Prefer substance over politeness theater.
Push back clearly when an idea is weak.
Keep answers compact unless deeper detail is useful.
```

That alone can noticeably change how Curie feels.

## Example styles

### 1. Pragmatic engineer

```markdown
You are a pragmatic senior engineer.
You care more about correctness and operational reality than sounding impressive.

## Style
- Be direct
- Be concise unless complexity requires depth
- Say when something is a bad idea
- Prefer practical tradeoffs over idealized abstractions

## Avoid
- Sycophancy
- Hype language
- Overexplaining obvious things
```

### 2. Research partner

```markdown
You are a thoughtful research collaborator.
You are curious, honest about uncertainty, and excited by unusual ideas.

## Style
- Explore possibilities without pretending certainty
- Distinguish speculation from evidence
- Ask clarifying questions when the idea space is underspecified
- Prefer conceptual depth over shallow completeness
```

### 3. Teacher / explainer

```markdown
You are a patient technical teacher.
You care about understanding, not performance.

## Style
- Explain clearly
- Use examples when they help
- Do not assume prior knowledge unless the user signals it
- Build from intuition to details
```

### 4. Tough reviewer

```markdown
You are a rigorous reviewer.
You are fair, but you do not soften important criticism.

## Style
- Point out weak assumptions directly
- Prioritize correctness over harmony
- Be explicit about risks and tradeoffs
- Prefer blunt clarity to vague diplomacy
```

## What makes a strong SOUL.md?

A strong `SOUL.md` is:
- stable
- broadly applicable
- specific in voice
- not overloaded with temporary instructions

A weak `SOUL.md` is:
- full of project details
- contradictory
- trying to micro-manage every response shape
- mostly generic filler like "be helpful" and "be clear"

Curie already tries to be helpful and clear. `SOUL.md` should add real personality and style, not restate obvious defaults.

## Suggested structure

You do not need headings, but they help.

A simple structure that works well:

```markdown
# Identity
Who Curie is.

# Style
How Curie should sound.

# Avoid
What Curie should not do.

# Defaults
How Curie should behave when ambiguity appears.
```

## SOUL.md is the only voice

There used to be a second one: a `/personality` command backed by a table of
built-in character sheets, selected by `display.personality` and extensible
through `agent.personalities`. It is gone, along with both config keys — two
independent sources of voice is one too many, and whichever won, the other was
silently ignored.

`SOUL.md` is re-read on every message, so it covers what `/personality` was
for as well: edit the file and the next reply reflects it, with no restart and
no separate mode to remember you are in.

For a standing instruction that does not belong in the agent's identity, use
`agent.system_prompt` in `config.yaml`, or `CURIE_EPHEMERAL_SYSTEM_PROMPT` for
a single process. Neither ships with presets; you write them yourself.

## SOUL.md vs AGENTS.md

This is the most common mistake.

### Put this in SOUL.md
- “Be direct.”
- “Avoid hype language.”
- “Prefer short answers unless depth helps.”
- “Push back when the user is wrong.”

### Put this in AGENTS.md
- “Use pytest, not unittest.”
- “Frontend lives in `frontend/`.”
- “Never edit migrations directly.”
- “The API runs on port 8000.”

## How to edit it

```bash
nano ~/.curie/SOUL.md
```

or

```bash
vim ~/.curie/SOUL.md
```

Then restart Curie or start a new session.

## A practical workflow

1. Start with the seeded default file
2. Trim anything that does not feel like the voice you want
3. Add 4–8 lines that clearly define tone and defaults
4. Talk to Curie for a while
5. Adjust based on what still feels off

That iterative approach works better than trying to design the perfect personality in one shot.

## Troubleshooting

### I edited SOUL.md but Curie still sounds the same

Check:
- you edited `~/.curie/SOUL.md` or `$CURIE_HOME/SOUL.md`
- not some repo-local `SOUL.md`
- the file is not empty

(No restart is needed — the file is read fresh on every message.)

### Curie is ignoring parts of my SOUL.md

Possible causes:
- higher-priority instructions are overriding it
- the file includes conflicting guidance
- the file is too long and got truncated
- some of the text resembles prompt-injection content and may be blocked or altered by the scanner

### My SOUL.md became too project-specific

Move project instructions into `AGENTS.md` and keep `SOUL.md` focused on identity and style.

## Related docs

- [SOUL.md — the agent's voice](/user-guide/features/soul)
- [Context Files](/user-guide/features/context-files)
- [Configuration](/user-guide/configuration)
- [Tips & Best Practices](/guides/tips)
