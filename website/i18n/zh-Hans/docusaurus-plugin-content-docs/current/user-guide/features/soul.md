---
sidebar_position: 9
title: "SOUL.md — agent 的声音"
description: "SOUL.md 是描述 Curie Agent 身份的唯一来源。编辑它，下一条回复即生效。"
---

# SOUL.md — agent 的声音

`SOUL.md` 是描述 Agent 身份的**唯一**来源。它位于系统提示词（prompt）的第 1 个槽位，
每条消息都会重新读取，编辑后下一条回复即生效——无需重启，也无需改动配置。

如果你想改变 Curie 的说话方式，或将其替换为完全不同的 Agent，请编辑这一个文件。

:::info 曾经有两处
早期版本还提供 `/personality` 命令，背后是一张内置人格表（"kawaii"、"pirate"、
"noir" 等），由 `display.personality` 选择，并可通过 `agent.personalities` 扩展。
两个互相独立的声音来源多了一个：无论哪一方生效，另一方都会被静默忽略，
而配置侧还会因残留的分散状态反复"复活"。

该人格表、两个配置键，以及各个界面上的 `/personality` 命令均已移除。
升级后首次运行会清除这些失效的键，并告知你移除了哪些内容。
`agent.system_prompt`——由你自己撰写的手动覆盖——不受影响，仍然可用。
:::

## SOUL.md 的工作方式

Curie 现在会自动在以下位置生成默认的 `SOUL.md`：

```text
~/.curie/SOUL.md
```

更准确地说，它使用当前实例的 `CURIE_HOME`，因此如果你以自定义主目录运行 Curie，它将使用：

```text
$CURIE_HOME/SOUL.md
```

### 重要行为

- **SOUL.md 是 Agent 的主要身份标识。** 它占据系统提示词的第 1 个槽位，替代硬编码的默认身份。
- 如果 `SOUL.md` 尚不存在，Curie 会自动创建一个初始文件
- 已有的用户 `SOUL.md` 文件不会被覆盖
- Curie 仅从 `CURIE_HOME` 加载 `SOUL.md`
- Curie 不会在当前工作目录中查找 `SOUL.md`
- 如果 `SOUL.md` 存在但为空，或无法加载，Curie 将回退到内置的默认身份
- 如果 `SOUL.md` 有内容，该内容在经过安全扫描和截断处理后将原样注入
- SOUL.md **不会**在上下文文件部分重复出现——它仅作为身份标识出现一次

这使 `SOUL.md` 成为真正的每用户或每实例身份标识，而不仅仅是一个附加层。

## 此设计的原因

这样可以保持个性的可预测性。

如果 Curie 从你启动它的任意目录加载 `SOUL.md`，你的个性可能会在不同项目之间意外改变。通过仅从 `CURIE_HOME` 加载，个性归属于 Curie 实例本身。

这也让用户更容易理解：
- "编辑 `~/.curie/SOUL.md` 来更改 Curie 的默认个性。"

## 编辑位置

对于大多数用户：

```bash
~/.curie/SOUL.md
```

如果你使用自定义主目录：

```bash
$CURIE_HOME/SOUL.md
```

## SOUL.md 应该写什么？

用于持久的语气和个性指导，例如：
- 语气
- 沟通风格
- 直接程度
- 默认交互风格
- 风格上应避免的内容
- Curie 应如何处理不确定性、分歧或模糊情况

不适合写入的内容：
- 一次性项目说明
- 文件路径
- 代码库规范
- 临时工作流细节

这些内容属于 `AGENTS.md`，而不是 `SOUL.md`。

## 优质 SOUL.md 内容

一个好的 SOUL 文件应该：
- 在不同上下文中保持稳定
- 足够宽泛，适用于多种对话场景
- 足够具体，能实质性地塑造语气
- 专注于沟通和身份，而非特定任务的指令

### 示例

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

## Curie 注入提示词的内容

`SOUL.md` 的内容直接进入系统提示词的第 1 个槽位——即 Agent 身份位置。不会在其周围添加任何包装语言。

内容会经过以下处理：
- 提示词注入扫描
- 内容过大时进行截断

如果文件为空、仅含空白字符或无法读取，Curie 将回退到内置默认身份（"You are Curie Agent, built by Nous Research. Be direct: match the length of your reply to the weight of the ask..."）。当 `skip_context_files` 被设置时（例如在子 Agent/委托上下文中），同样适用此回退。

## 安全扫描

`SOUL.md` 与其他携带上下文的文件一样，在被包含前会进行提示词注入模式扫描。

这意味着你仍应将其专注于角色/语气，而不是试图混入奇怪的元指令。

## SOUL.md 与 AGENTS.md

这是最重要的区别。

### SOUL.md
用于：
- 身份
- 语气
- 风格
- 沟通默认值
- 个性层面的行为

### AGENTS.md
用于：
- 项目架构
- 编码规范
- 工具偏好
- 代码库特定工作流
- 命令、端口、路径、部署说明

一个实用的判断规则：
- 如果它应该随你到处适用，属于 `SOUL.md`
- 如果它属于某个项目，属于 `AGENTS.md`

## 手动覆盖

`~/.curie/config.yaml` 中的 `agent.system_prompt` 会追加在 `SOUL.md` 之上。
它不是"人格"：不附带任何预设，代码中也没有任何地方会写入它——
它的用途是承载那些不便写成身份描述的常驻指令。

```yaml
agent:
  system_prompt: >
    使用公制单位作答，优先使用 SI 符号而非全称。
```

`CURIE_EPHEMERAL_SYSTEM_PROMPT` 作用相同，但只影响单个进程，且优先级高于配置值：

```bash
CURIE_EPHEMERAL_SYSTEM_PROMPT="本次会话请用西班牙语回复。" curie chat
```

大多数场景两者都不需要。优先把想要的内容写进 `SOUL.md`——那是一个你可以直接读的文件。

## 恢复默认

删除或清空该文件，Curie 会回退到内置身份：

```bash
rm ~/.curie/SOUL.md      # 下次运行时以默认内容重新生成
```

若想保留自己的版本，先移开它：

```bash
mv ~/.curie/SOUL.md ~/.curie/SOUL.md.bak
```

## 推荐工作流

1. 在 `~/.curie/SOUL.md` 中写一份经过思考的内容——语气、篇幅、需要避免什么。
2. 项目相关的说明放进该项目的 `AGENTS.md`，而不是这里。
3. 只有当某条指令确实不属于 Agent 身份时，才使用 `agent.system_prompt`。

这样你会得到稳定的声音、归属正确的项目行为，以及一个想改变说话方式时要编辑的文件。

## SOUL.md 在完整提示词中的位置

1. **SOUL.md** — Agent 身份；为空时使用内置默认值
2. 工具相关的行为指引
3. 记忆 / 用户上下文
4. Skills 指引
5. 上下文文件（`AGENTS.md`、`.cursorrules`）
6. 时间戳
7. 平台相关的格式提示
8. 已设置时的 `agent.system_prompt` / `CURIE_EPHEMERAL_SYSTEM_PROMPT`

`SOUL.md` 是基础——其余内容都构建在它之上。

## 声音与外观是两件事

Curie 如何**说话**与如何**显示**是彼此独立的设置：

- `SOUL.md` 和 `agent.system_prompt` 影响它如何说话
- `display.skin` 和 `/skin` 影响它在终端中的外观

终端外观请参阅 [Skins & Themes](./skins.md)。

## 相关文档

- [上下文文件](/user-guide/features/context-files)
- [配置](/user-guide/configuration)
- [技巧与最佳实践](/guides/tips)
- [SOUL.md 指南](/guides/use-soul-with-curie)
- [Skins & Themes](./skins.md)
