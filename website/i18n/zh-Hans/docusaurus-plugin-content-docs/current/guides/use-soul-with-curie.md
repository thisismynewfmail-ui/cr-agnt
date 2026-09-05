---
sidebar_position: 7
title: "在 Curie 中使用 SOUL.md"
description: "如何使用 SOUL.md 塑造 Curie Agent 的风格，哪些内容应放在其中，以及它与 AGENTS.md 的区别"
---

# 在 Curie 中使用 SOUL.md

`SOUL.md` 是你的 Curie 实例的**主要身份标识**。它是系统提示词（system prompt）中的第一项内容——定义了 Agent 是谁、如何表达，以及应避免什么。

如果你希望每次与 Curie 交谈时都感受到一致的助手风格，或者想用自己的角色完全替换 Curie 的默认人设，这就是你需要编辑的文件。

## SOUL.md 的用途

`SOUL.md` 适用于：
- 语气
- 个性
- 沟通风格
- Curie 应有多直接或多温和
- Curie 在风格上应避免什么
- Curie 如何应对不确定性、分歧和模糊情况

简而言之：
- `SOUL.md` 关注的是 Curie 是谁，以及 Curie 如何表达

## SOUL.md 不适用的内容

不要在其中放置：
- 特定代码仓库的编码规范
- 文件路径
- 命令
- 服务端口
- 架构说明
- 项目工作流指令

这些内容属于 `AGENTS.md`。

一个简单的判断原则：
- 如果某项内容应在所有地方生效，放入 `SOUL.md`
- 如果某项内容只属于某个项目，放入 `AGENTS.md`

## 文件位置

Curie 目前仅使用当前实例的全局 SOUL 文件：

```text
~/.curie/SOUL.md
```

如果你使用自定义主目录运行 Curie，路径变为：

```text
$CURIE_HOME/SOUL.md
```

## 首次运行行为

如果 `SOUL.md` 尚不存在，Curie 会自动为你生成一个初始文件。

这意味着大多数用户一开始就有一个可以立即阅读和编辑的真实文件。

注意：
- 如果你已有 `SOUL.md`，Curie 不会覆盖它
- 如果文件存在但为空，Curie 不会从中向提示词添加任何内容

## Curie 如何使用它

Curie 启动会话时，会从 `CURIE_HOME` 读取 `SOUL.md`，扫描其中的提示词注入（prompt-injection）模式，必要时进行截断，并将其作为 **Agent 身份标识**——系统提示词中的第 1 个槽位。这意味着 `SOUL.md` 会完全替换内置的默认身份文本。

如果 `SOUL.md` 缺失、为空或无法加载，Curie 将回退到内置的默认身份。

文件内容不会被任何包装语言包裹。内容本身才是关键——按照你希望 Agent 思考和表达的方式来写。

## 第一次编辑建议

如果你只做一件事，打开文件并修改几行，让它感觉像你自己的风格。

例如：

```markdown
You are direct, calm, and technically precise.
Prefer substance over politeness theater.
Push back clearly when an idea is weak.
Keep answers compact unless deeper detail is useful.
```

仅此一项就能明显改变 Curie 的感觉。

## 示例风格

### 1. 务实工程师

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

### 2. 研究伙伴

```markdown
You are a thoughtful research collaborator.
You are curious, honest about uncertainty, and excited by unusual ideas.

## Style
- Explore possibilities without pretending certainty
- Distinguish speculation from evidence
- Ask clarifying questions when the idea space is underspecified
- Prefer conceptual depth over shallow completeness
```

### 3. 教师／讲解者

```markdown
You are a patient technical teacher.
You care about understanding, not performance.

## Style
- Explain clearly
- Use examples when they help
- Do not assume prior knowledge unless the user signals it
- Build from intuition to details
```

### 4. 严格审阅者

```markdown
You are a rigorous reviewer.
You are fair, but you do not soften important criticism.

## Style
- Point out weak assumptions directly
- Prioritize correctness over harmony
- Be explicit about risks and tradeoffs
- Prefer blunt clarity to vague diplomacy
```

## 什么是优质的 SOUL.md？

优质的 `SOUL.md` 具备以下特点：
- 稳定
- 广泛适用
- 风格具体
- 不堆砌临时指令

劣质的 `SOUL.md` 则是：
- 充斥项目细节
- 自相矛盾
- 试图微观管理每一个回复的形式
- 大量泛泛之词，如"要有帮助"和"要清晰"

Curie 本身已经尽力做到有帮助且清晰。`SOUL.md` 应当赋予真实的个性和风格，而不是重申显而易见的默认行为。

## 建议结构

不需要标题，但标题有助于组织内容。

一个实用的简单结构：

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

## SOUL.md 是唯一的声音来源

早期版本还有第二处：一个 `/personality` 命令，背后是一张内置人格表，
由 `display.personality` 选择，可通过 `agent.personalities` 扩展。
该命令与两个配置键均已移除——两个互相独立的声音来源多了一个，
而且无论哪一方生效，另一方都会被静默忽略。

`SOUL.md` 每条消息都会重新读取，因此它也覆盖了 `/personality` 的用途：
编辑文件，下一条回复即生效，无需重启，也不必记住自己处于哪种临时模式。

若需要一条不属于 agent 身份的常驻指令，请使用 `config.yaml` 中的
`agent.system_prompt`，或用 `CURIE_EPHEMERAL_SYSTEM_PROMPT` 只作用于单个进程。

## SOUL.md 与 AGENTS.md 的区别

这是最常见的误用。

### 放入 SOUL.md 的内容
- "Be direct."
- "Avoid hype language."
- "Prefer short answers unless depth helps."
- "Push back when the user is wrong."

### 放入 AGENTS.md 的内容
- "Use pytest, not unittest."
- "Frontend lives in `frontend/`."
- "Never edit migrations directly."
- "The API runs on port 8000."

## 如何编辑

```bash
nano ~/.curie/SOUL.md
```

或

```bash
vim ~/.curie/SOUL.md
```

然后重启 Curie 或开启新会话。

## 实用工作流

1. 从自动生成的默认文件开始
2. 删除不符合你期望风格的内容
3. 添加 4–8 行清晰定义语气和默认行为的文字
4. 与 Curie 交谈一段时间
5. 根据仍感觉不对的地方进行调整

这种迭代方式比一次性设计完美人设更有效。

## 故障排查

### 我编辑了 SOUL.md，但 Curie 听起来还是一样

检查：
- 你编辑的是 `~/.curie/SOUL.md` 或 `$CURIE_HOME/SOUL.md`
- 而不是某个仓库本地的 `SOUL.md`
- 文件不为空
- 编辑后已重启会话

### Curie 忽略了我 SOUL.md 中的部分内容

可能原因：
- 更高优先级的指令覆盖了它
- 文件中包含相互冲突的指导内容
- 文件过长被截断
- 部分文本类似提示词注入内容，可能被扫描器拦截或修改

### 我的 SOUL.md 变得过于项目化

将项目指令移入 `AGENTS.md`，保持 `SOUL.md` 专注于身份标识和风格。

## 相关文档

- [个性与 SOUL.md](/user-guide/features/soul)
- [上下文文件](/user-guide/features/context-files)
- [配置](/user-guide/configuration)
- [技巧与最佳实践](/guides/tips)