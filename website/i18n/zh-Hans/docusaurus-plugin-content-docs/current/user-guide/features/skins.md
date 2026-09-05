---
sidebar_position: 10
title: "皮肤与主题"
description: "使用内置和用户自定义皮肤定制 Curie CLI 的外观"
---

# 皮肤与主题

皮肤控制 Curie 的**视觉呈现**：横幅颜色、spinner（加载动画）面孔与动词、
响应框标签、品牌文本以及工具活动前缀。一个皮肤会同时应用到三个界面——
经典 CLI、TUI，以及[工作台控制台](#工作台控制台)——切换一次即可。

Curie 如何**说话**与如何**显示**是彼此独立的设置：

- **[SOUL.md](./soul.md)** 是 agent 的声音——语气与措辞。
- **Skin（皮肤）** 是它的外观。

## 切换皮肤

```bash
/skin                # 显示当前皮肤并列出可用皮肤
/skin curie-vga      # 切换到内置皮肤
/skin mytheme        # 切换到 ~/.curie/skins/mytheme.yaml 中的自定义皮肤
```

或在 `~/.curie/config.yaml` 中设置默认皮肤：

```yaml
display:
  skin: default
```

## 内置皮肤

默认皮肤是**工作台（bench）**：在终端里呈现的一台二十世纪中叶的仪表面板。
珐琅底色、石墨墨色、取自警示标牌的信号色，以及用 IBM code page 437 的
制表符与阴影字符绘制的边框——四级灰度、若干种线宽，这正是当年的文本模式
程序所能用来构建界面的全部素材。

它是为**浅色终端**设计的。所有文字颜色对白色背景的对比度都不低于 3:1，
正文墨色达到 14.4:1，因此在大多数终端默认的银白色背景上依然清晰。
每个成对皮肤还带有一份为相反明暗极性手工调校的色板，
因此深色终端得到的是"同一台面板在台灯下"的样子，而不是自动反色的结果。

| 皮肤 | 描述 | 视觉特征 |
|------|------|---------|
| `default` | Curie 工作台 — 暖珐琅与模板字体信号色 | 默认外观。珐琅银白底上的焦琥珀与氧化红，黄铜镶边，石墨色正文。Spinner 动词取自实验台工作："calibrating"、"weighing out"、"cross-checking"、"reading the dial"。横幅主图是一段走纸记录仪曲线。 |
| `curie-nightbench` | 同一台面板在台灯下 | 把工作台色板的深色一半提升为独立皮肤，适合无论终端如何上报都想要深色的场景。 |
| `curie-vga` | IBM VGA 的那十六色 | 不是"灵感来源于"——就是 VGA 显卡默认文本色板实际输出的那十六种颜色。Turbo Vision 蓝、模板白、双线边框。状态栏放在黑底上，因为亮红与亮品红只有在黑底上才能达到 3:1；当年的 DOS 状态栏正是出于同样的原因，用亮色系画在深色单元格上。 |
| `curie-amber` | 琥珀荧光 | 整个界面只用一种色相，层次完全靠亮度区分——这正是 IBM 5151 一类琥珀显示器所能提供的全部，也是琥珀终端在任何字号下都清晰可读的原因。 |
| `curie-blueprint` | 蓝晒图 | 用接触印相复制的工程图纸。浅色下是绘图纸上的普鲁士蓝墨线，深色下是普鲁士蓝底上的白线。 |
| `mono` | 单色 — 简洁灰度 | 全灰色，无彩色。适合极简终端或录屏场景。 |
| `slate` | 冷蓝色 — 面向开发者 | 皇家蓝边框，柔和蓝色文字。沉稳专业。 |
| `daylight` | 冷蓝点缀的浅色主题 | 深石板色文字配蓝色边框与浅色状态面板，专为白色或亮色终端设计。 |
| `warm-lightmode` | 适用于浅色背景的暖棕/金色 | 暖羊皮纸色调——深棕色文字、马鞍棕点缀、奶油色状态面板。比 `daylight` 更偏大地色系。 |

:::note 已移除的皮肤
`ares`、`poseidon`、`sisyphus` 和 `charizard` 已经移除。它们的命名来自 Agent
先前的身份——Hermes 所属的希腊神系，以及在此之上的一个吉祥物玩笑。
`mono`、`slate`、`daylight` 和 `warm-lightmode` 保留：它们描述的是终端明暗
极性，而不是某个身份。
:::

## 工作台控制台

`curie ui` 会打开一个全屏、可用鼠标操作的控制台，其配色来自当前皮肤。
在它的 **PANEL** 面板中点击某个皮肤即可全局应用——控制台会重绘，
CLI 与 TUI 也会随之更新。

```bash
curie ui                    # 依据终端自身的提示判断明暗
curie ui --polarity dark    # 强制使用深色一半
curie ui --polarity light   # 强制使用浅色一半
```

明暗极性的判定顺序为：`CURIE_UI_POLARITY`，然后是 `COLORFGBG`
（xterm 系终端会以 `fg;bg` 的 ANSI 索引形式设置它），最后回退到浅色。

## 字标缩放

横幅字标提供三种尺寸，系统会按字标自身的宽度测量，选择能放得下的最大一种：

| 终端宽度 | 呈现内容 |
|----------|---------|
| 86 列及以上 | 完整方块字体的 `CURIE-AGENT` |
| 36–85 列 | 仅 `CURIE`，同样的方块字体 |
| 20–35 列 | 一条模板字体的横线 |
| 不足 20 列 | 不绘制——每一行留给内容更有价值 |

自定义皮肤可以提供 `banner_logo`、`banner_logo_medium` 和
`banner_logo_compact`；未提供的尺寸会回退到下一个更小的。宽度由字标本身测得，
因此更窄或更宽的字标都会被正确归档，无需额外声明。

## 可配置键完整列表

### 颜色（`colors:`）

控制 CLI 中所有颜色值。值为十六进制颜色字符串。

| 键 | 描述 | 默认值（`default` 皮肤） |
|----|------|------------------------|
| `banner_border` | 启动横幅周围的面板边框 | `#CD7F32`（青铜色） |
| `banner_title` | 横幅中的标题文字颜色 | `#FFD700`（金色） |
| `banner_accent` | 横幅中的区块标题（Available Tools 等） | `#FFBF00`（琥珀色） |
| `banner_dim` | 横幅中的弱化文字（分隔符、次要标签） | `#B8860B`（暗金菊色） |
| `banner_text` | 横幅中的正文文字（工具名、技能名） | `#FFF8DC`（玉米丝色） |
| `ui_accent` | 通用 UI 强调色（高亮、活动元素） | `#FFBF00` |
| `ui_label` | UI 标签与标记 | `#4dd0e1`（青色） |
| `ui_ok` | 成功指示器（对勾、完成） | `#4caf50`（绿色） |
| `ui_error` | 错误指示器（失败、阻断） | `#ef5350`（红色） |
| `ui_warn` | 警告指示器（注意、审批提示） | `#ffa726`（橙色） |
| `prompt` | 交互式 prompt（提示符）文字颜色 | `#FFF8DC` |
| `input_rule` | 输入区域上方的水平分隔线 | `#CD7F32` |
| `response_border` | agent 响应框边框（ANSI 转义） | `#FFD700` |
| `session_label` | 会话标签颜色 | `#DAA520` |
| `session_border` | 会话 ID 弱化边框颜色 | `#8B8682` |
| `status_bar_bg` | TUI 状态/用量栏的背景色 | `#1a1a2e` |
| `voice_status_bg` | 语音模式状态徽章的背景色 | `#1a1a2e` |
| `selection_bg` | TUI 鼠标选区高亮的背景色。未设置时回退到 `completion_menu_current_bg`。 | `#333355` |
| `completion_menu_bg` | 补全菜单列表的背景色 | `#1a1a2e` |
| `completion_menu_current_bg` | 当前活动补全行的背景色 | `#333355` |
| `completion_menu_meta_bg` | 补全元信息列的背景色 | `#1a1a2e` |
| `completion_menu_meta_current_bg` | 当前活动补全元信息列的背景色 | `#333355` |

### Spinner（`spinner:`）

控制等待 API 响应时显示的动画 spinner。

| 键 | 类型 | 描述 | 示例 |
|----|------|------|------|
| `waiting_faces` | 字符串列表 | 等待 API 响应时循环显示的面孔 | `["(⚔)", "(⛨)", "(▲)"]` |
| `thinking_faces` | 字符串列表 | 模型推理期间循环显示的面孔 | `["(⚔)", "(⌁)", "(<>)"]` |
| `thinking_verbs` | 字符串列表 | spinner 消息中显示的动词 | `["forging", "plotting", "hammering plans"]` |
| `wings` | [左, 右] 对的列表 | spinner 周围的装饰括号 | `[["⟪⚔", "⚔⟫"], ["⟪▲", "▲⟫"]]` |

当 spinner 值为空时（如 `default` 和 `mono`），将使用 `display.py` 中的硬编码默认值。

### 品牌（`branding:`）

CLI 界面中使用的文字字符串。

| 键 | 描述 | 默认值 |
|----|------|--------|
| `agent_name` | 横幅标题和状态显示中的名称 | `Curie Agent` |
| `welcome` | CLI 启动时显示的欢迎消息 | `Welcome to Curie Agent! Type your message or /help for commands.` |
| `goodbye` | 退出时显示的消息 | `Goodbye! ▮` |
| `response_label` | 响应框标题上的标签 | ` ▮ Curie ` |
| `prompt_symbol` | 用户输入 prompt 前的符号（裸 token，渲染器会在后面添加空格） | `❯` |
| `help_header` | `/help` 命令输出的标题文字 | `(^_^)? Available Commands` |

### 其他顶级键

| 键 | 类型 | 描述 | 默认值 |
|----|------|------|--------|
| `tool_prefix` | 字符串 | CLI 中工具输出行的前缀字符 | `┊` |
| `tool_emojis` | 字典 | 各工具的 emoji 覆盖，用于 spinner 和进度显示（`{tool_name: emoji}`） | `{}` |
| `banner_logo` | 字符串 | Rich 标记 ASCII 艺术 logo（替换默认的 CURIE_AGENT 横幅） | `""` |
| `banner_hero` | 字符串 | Rich 标记英雄艺术图（替换默认的双蛇杖图案） | `""` |

## 自定义皮肤

在 `~/.curie/skins/` 下创建 YAML 文件。用户皮肤会从内置 `default` 皮肤继承缺失的值，因此只需指定要更改的键。

### 完整自定义皮肤 YAML 模板

```yaml
# ~/.curie/skins/mytheme.yaml
# Complete skin template — all keys shown. Delete any you don't need;
# missing values automatically inherit from the 'default' skin.

name: mytheme
description: My custom theme

colors:
  banner_border: "#CD7F32"
  banner_title: "#FFD700"
  banner_accent: "#FFBF00"
  banner_dim: "#B8860B"
  banner_text: "#FFF8DC"
  ui_accent: "#FFBF00"
  ui_label: "#4dd0e1"
  ui_ok: "#4caf50"
  ui_error: "#ef5350"
  ui_warn: "#ffa726"
  prompt: "#FFF8DC"
  input_rule: "#CD7F32"
  response_border: "#FFD700"
  session_label: "#DAA520"
  session_border: "#8B8682"
  status_bar_bg: "#1a1a2e"
  voice_status_bg: "#1a1a2e"
  selection_bg: "#333355"
  completion_menu_bg: "#1a1a2e"
  completion_menu_current_bg: "#333355"
  completion_menu_meta_bg: "#1a1a2e"
  completion_menu_meta_current_bg: "#333355"

spinner:
  waiting_faces:
    - "(⚔)"
    - "(⛨)"
    - "(▲)"
  thinking_faces:
    - "(⚔)"
    - "(⌁)"
    - "(<>)"
  thinking_verbs:
    - "processing"
    - "analyzing"
    - "computing"
    - "evaluating"
  wings:
    - ["⟪⚡", "⚡⟫"]
    - ["⟪●", "●⟫"]

branding:
  agent_name: "My Agent"
  welcome: "Welcome to My Agent! Type your message or /help for commands."
  goodbye: "See you later! ⚡"
  response_label: " ⚡ My Agent "
  prompt_symbol: "⚡"
  help_header: "(⚡) Available Commands"

tool_prefix: "┊"

# Per-tool emoji overrides (optional)
tool_emojis:
  terminal: "⚔"
  web_search: "🔮"
  read_file: "📄"

# Custom ASCII art banners (optional, Rich markup supported)
# banner_logo: |
#   [bold #FFD700] MY AGENT [/]
# banner_hero: |
#   [#FFD700]  Custom art here  [/]
```

### 最简自定义皮肤示例

由于所有值都继承自 `default`，最简皮肤只需指定要更改的部分：

```yaml
name: cyberpunk
description: Neon terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

## Curie Mod — 可视化皮肤编辑器

[Curie Mod](https://github.com/cocktailpeanut/curie-mod) 是一个社区构建的 Web UI，用于可视化创建和管理皮肤。无需手写 YAML，提供带实时预览的点击式编辑器。

![Curie Mod skin editor](https://raw.githubusercontent.com/cocktailpeanut/curie-mod/master/nous.png)

**功能说明：**

- 列出所有内置和自定义皮肤
- 将任意皮肤在可视化编辑器中打开，涵盖所有 Curie 皮肤字段（颜色、spinner、品牌、工具前缀、工具 emoji）
- 根据文字 prompt 生成 `banner_logo` 文字艺术
- 将上传的图片（PNG、JPG、GIF、WEBP）转换为 `banner_hero` ASCII 艺术，支持多种渲染风格（盲文点阵、ASCII 字符渐变、方块、点阵）
- 直接保存到 `~/.curie/skins/`
- 通过更新 `~/.curie/config.yaml` 激活皮肤
- 显示生成的 YAML 及实时预览

### 安装

**方式一 — Pinokio（一键安装）：**

在 [pinokio.computer](https://pinokio.computer) 上找到并一键安装。

**方式二 — npx（终端最快方式）：**

```bash
npx -y curie-mod
```

**方式三 — 手动安装：**

```bash
git clone https://github.com/cocktailpeanut/curie-mod.git
cd curie-mod/app
npm install
npm start
```

### 使用方法

1. 启动应用（通过 Pinokio 或终端）。
2. 打开 **Skin Studio**。
3. 选择要编辑的内置或自定义皮肤。
4. 从文字生成 logo，和/或上传图片作为英雄艺术图。选择渲染风格和宽度。
5. 编辑颜色、spinner、品牌及其他字段。
6. 点击 **Save** 将皮肤 YAML 写入 `~/.curie/skins/`。
7. 点击 **Activate** 将其设为当前皮肤（更新 `config.yaml` 中的 `display.skin`）。

Curie Mod 遵循 `CURIE_HOME` 环境变量，因此也适用于[配置文件](/user-guide/profiles)。

## 操作说明

- 内置皮肤从 `curie_cli/skin_engine.py` 加载。
- 未知皮肤自动回退到 `default`。
- `/skin` 立即更新当前会话的活动 CLI 主题。
- `~/.curie/skins/` 中的用户皮肤优先于同名内置皮肤。
- 通过 `/skin` 切换皮肤仅对当前会话有效。如需永久设为默认皮肤，请在 `config.yaml` 中配置。
- `banner_logo` 和 `banner_hero` 字段支持 Rich 控制台标记（例如 `[bold #FF0000]text[/]`），可用于彩色 ASCII 艺术。