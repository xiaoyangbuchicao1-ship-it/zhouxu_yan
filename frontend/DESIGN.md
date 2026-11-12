---
name: "舟序 Zhouxu"
description: "既有舟序品牌下、对话优先的海事研究桌面视觉系统"
colors:
  primary: "#dce7f3"
  primary-ink: "#111c29"
  primary-hover: "#f0f5fa"
  focus: "#89aacc"
  canvas: "#080c10"
  sidebar: "#0d1117"
  surface: "#121820"
  raised: "#1b232d"
  text: "#e6e9ee"
  muted: "#9da9b8"
  line: "#232c37"
  secondary: "#242f3d"
  secondary-ink: "#d7e1ed"
typography:
  display:
    fontFamily: "'Instrument Serif', serif"
    fontWeight: 400
    lineHeight: 1
  headline:
    fontFamily: "'Noto Serif SC', 'Source Han Serif SC', serif"
    fontSize: "32px"
    fontWeight: 500
    lineHeight: 1.6
    letterSpacing: "-0.02em"
  title:
    fontFamily: "'Noto Sans SC', 'Microsoft YaHei', sans-serif"
    fontSize: "24px"
    fontWeight: 500
  body:
    fontFamily: "'Noto Sans SC', 'Microsoft YaHei', sans-serif"
    fontSize: "16px"
    lineHeight: 1.85
  label:
    fontFamily: "'Noto Sans SC', 'Microsoft YaHei', sans-serif"
    fontSize: "14px"
rounded:
  compact: "4px"
  control: "8px"
  floating: "12px"
  conversation: "16px"
spacing:
  tight: "8px"
  compact: "12px"
  regular: "16px"
  section: "24px"
  spacious: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-icon:
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    width: "32px"
    height: "32px"
  field:
    backgroundColor: "#0e151e"
    textColor: "#e3edf8"
    rounded: "{rounded.control}"
    padding: "11px 12px"
  composer:
    backgroundColor: "#141b24"
    rounded: "{rounded.conversation}"
    padding: "18px 18px 12px"
  attachment:
    backgroundColor: "#202c39"
    rounded: "{rounded.control}"
    padding: "9px 10px"
---

# Design System: 舟序 Zhouxu

## Overview

**Creative North Star: "Conversation-first Maritime Research Desk"**

舟序以近黑底、分层炭灰和灰蓝焦点建立安静、专业的研究环境。工作界面让中文问题、长篇分析和材料来源先于界面装饰被看见；帆形标识与斜体 Zhouxu 字标衔接既有品牌，而不是另立身份。

本文记录已实现代码。共同身份来自自托管 Noto Sans SC、Noto Serif SC、Instrument Serif 与既有标识；下方尺寸、形状、密度及组件 token 默认仅约束工作台、其门户菜单与弹层。已认可的落地页保留大字标、海面视频、胶囊 CTA 与较长入场动画；认证页保留自己的背景、表单与胶囊提交按钮，不能套用工作台尺寸重新设计。工作台方向已获用户批准，标识为 USER-PINNED-ZHOUXU-WORKSPACE-20261021，不是随机风格种子。

**Key Characteristics:**

- 对话与阅读优先，材料和历史作为可收放的工作支撑。
- 近黑分层、灰蓝状态、浅色实心主操作。
- 中文工作字体清晰，衬线欢迎语与斜体字标各司其职。
- 常态保留连续、可打断的动效；系统减少动态效果时提供回退。

证据：`src/index.css`、`src/components/Hero.tsx`、`src/pages/auth.css`、`src/pages/workspace.css` 及 `src/components/workspace/`。产品事实见 PRODUCT.md；用户工作台构图与任务边界见 PRODUCT.md 和 `docs/harness-integration.md`；方向合同在 index.html 的首个 body 注释。本文不替代这些记录。

## Colors

近黑与灰蓝共同构成冷静的海事研究环境；状态明确但不把每个工作区块做成彩色卡片。

### Primary

- **Pale Action**：primary / primary-ink 用于工作台主要文字按钮；悬停变为 primary-hover。发送、新对话有同一浅色家族的既有局部值，保留其差异。
- **Gray-blue Focus**：focus 用于键盘焦点与研究状态强调，不代替所有主操作的浅色实底。

### Secondary

- **Charcoal Action**：secondary / secondary-ink 用于次操作；它是中性色动作角色，不代表新增品牌色。

### Neutral

- **Deep Canvas**：canvas 是主阅读区的连续底色。
- **Sidebar Charcoal**：sidebar 承载历史、项目与账户。
- **Quiet Surface / Raised Charcoal**：surface、raised 用于内部层级；弹层、输入区与菜单另有同家族的局部实现色，不强制抹平。
- **Reading Text / Quiet Text**：text 与 muted 区分正文和辅助信息；muted 不应通过进一步降低透明度替代层级。
- **Quiet Line**：line 表达微弱分隔；现有资料列表、字段描边保留各自对比度。

**The Pale Action Rule.** 工作台主操作使用浅色实心，灰蓝主要承担焦点和选择状态；不将主操作改成铺满屏幕的饱和蓝。

## Typography

**Display Font:** Instrument Serif，serif 回退；身份字标使用 italic。
**Body Font:** Noto Sans SC，Microsoft YaHei、sans-serif 回退。
**Editorial Font:** Noto Serif SC，Source Han Serif SC、serif 回退。

三种字体由 src/index.css 自托管。工作字体服务密集中文，衬线只在欢迎语、空态和品牌身份上形成温度，不让正文成为展示字体试验。

### Hierarchy

- **Display**：工作台字标（32px）、回答标记（24px），斜体、紧凑单行；frontmatter 不规定统一字号，因为既有落地页字标使用响应式巨型字号。
- **Headline**：工作台欢迎语使用 headline；移动端变为（24px，行高 1.7）。这是工作台角色，不改认证页标题。
- **Title**：资料库标题（24px，500）；弹层标题与回答二级标题（20px，500，行高 1.6）。
- **Body**：分析正文使用 body，阅读列最大（720px）；输入文字（16px，行高 1.8），用户提问（16px，行高 1.8）。
- **Label**：导航、状态、材料元数据（14px）；常态 400，主按钮和必要标题 500；回答重点可到 600。
- **Code**：仅技术片段使用 ui-monospace、monospace（14px），不替换界面字体。

**The Working Type Rule.** 工作台保留 14 / 16 / 20 / 24 / 32 的清楚层级；长篇分析使用正文角色，品牌斜体与欢迎衬线不侵入连续阅读。

## Layout

工作台是满视口 flex 工作区域（100dvh），不是统计仪表盘。桌面历史与项目侧栏展开宽（248px）、收起宽（76px）；主阅读区可收缩且自有滚动，资料面板按需展开（340px），初始关闭。顶部工具栏（80px），阅读列最大（720px），输入区最大（760px）；资料库页面最大（1160px）。

间距以 frontmatter 的重复步长为基础，阅读区桌面水平留白（36px）、资料库（60px）是现有容器规则，不应把所有局部值机械吸附到一个网格。输入区固定在对话流之外，长回答滚动不挤走输入；空态欢迎区与输入区居中，首轮提问后连续转入常态。

响应式存在两个独立阈值：不超过（1100px）时资料从并排面板转为焦点管理的抽屉，同时收紧留白；不超过（760px）时桌面侧栏隐藏、顶部变为（68px）、导航以左侧抽屉打开、欢迎标题缩小。资料抽屉最大（400px），左导航抽屉最大（300px）且预留屏幕边缘；小高度（600px）空态允许滚动。移动端输入区考虑 safe-area-inset-bottom，不整体缩放桌面。

**The Reading Lane Rule.** 历史和材料可以收放，但正文阅读列与输入区始终是主工作路径；不以虚构统计卡填充留白。

## Elevation & Depth

常驻区域靠近黑色阶和少量分隔线建立深度，不靠每张卡片的阴影。阅读区与输入区之间的短渐隐只表达滚动边界，不是装饰性全屏渐变。浮层在真实遮罩之上获得阴影；认证页已存在的氛围背景和落地页视频不受此工作台规则否定。

### Shadow Vocabulary

- **Dialog**（`0 24px 72px #0007`）：对话框层级。
- **Menu**（`0 10px 32px #0007`）：触发点附近的菜单。
- **Notice**（`0 10px 32px #0006`）：固定反馈。
- **Latest**（`0 6px 16px #0004`）：回到最新内容的浮动控制。

**The Tonal Desk Rule.** 工作台常驻平面靠色阶组织，阴影留给浮动反馈与菜单、弹层；不把此规则推广成对原落地页或认证背景的禁令。

## Shapes

工作台控制与导航以 control 圆角为主；小复选框和行内代码使用 compact，菜单、通知、代码块使用 floating，对话气泡、输入区和模态框使用 conversation。发送按钮保留局部（9px）圆角，分段标签保留（6px），这些不是新增通用尺度。头像和帆形标识为圆形；移动抽屉贴边无圆角。原认证页及落地页的胶囊按钮继续保留，不能按工作台圆角统一。

## Components

### Buttons

克制、直接、状态清晰。主按钮与次按钮使用 frontmatter 对应组件，最小高度（42px）、500 字重。主按钮悬停变浅；次按钮悬停背景（#34475d）。图标按钮（32px 方形）保持明确 aria-label，沿用 Lucide 描边图标，不使用文字字形模拟图标。危险操作是局部暖红确认，不建立第二套品牌色。禁用按钮不可点击并变弱；发送按钮有自己的实心禁用态，不能靠透明度让其误似可发送。

### Navigation

新对话与资料库入口高（44px）、内部间隔（12px）；项目、历史为紧凑列表而非指标卡。当前对话背景通过共享布局连续移动，选中态同时改变文字和底色。精确指针下行操作可在 hover 或 focus-within 时显现；触屏不依赖 hover。侧栏与弹层内账户、项目菜单使用相同焦点语言。

### Inputs / Fields

命名字段使用 field token，描边（1px solid #53657a）；资料搜索使用底线输入，不给每个搜索框强加外框。工作台键盘焦点为（2px solid focus），偏移（3px）；输入区通过 focus-within 提升外框而非给 textarea 内部叠第二层边框。占位符不是标签，保持现有可访问名称。

### Chips / Attachments

附件是可操作的材料小容器：图标、文件名、状态和移除按钮，不是装饰性标签。使用 attachment token；文件名截断但布局可收缩，移动端可占整行。失败项用暖色状态与文字说明，不仅改变颜色。

### Cards / Containers

工作台没有通用统计卡格；反复出现的容器是 composer、对话气泡、材料分组、浮动菜单和弹层。它们各自拥有与任务匹配的边框、内边距和背景；不能因文档需要 card 样例而虚构仪表盘卡片。

### Composer

复合输入区采用 composer token 与（1px solid #354252）描边，focus-within 变为（#7594b5）；正文输入（16px），高度随内容在现有限制内增长。发送（36px 方形）与停止是不同状态，工具栏保持可达；欢迎退场、输入位置转换、附件进出都保留连续过渡。

### Materials & Sources

当前对话与资料库选择是两个有语义的标签页，方向键、Home、End 可导航，活动背景以弹簧移动。选择行有选中复选标记及 aria-pressed；“已选资料”说明为“可供本对话使用，实际引用以回答中的来源为准。”资料正文预览是读取片段，不冒充原文件预览。选择不等于引用、上传不等于使用，视觉状态不得混淆。

### Motion & Focus-managed Layers

常态按钮颜色（160ms）、按压（140ms，scale 0.975）、输入区边框（200ms）；欢迎进入（280ms）、退出（120ms）、对话切换（180ms）；侧栏和资料展开（240ms）。菜单进入（160ms）、退出（110ms），弹层进入（220ms）、退出（140ms）。欢迎、输入位置、活动历史和材料标签的弹簧/布局动画共同维持顺滑，不能简化成几乎静态。弹簧参数与具体缓动保存在 sidecar。

Radix 模态框、菜单与抽屉保留键盘操作、Escape 关闭与关闭后触发点焦点恢复。减少动态效果模式由全局 CSS、MotionConfig、useReducedMotion 配合，禁用/缩短动画、移除按压位移，并取消自动滚动中的平滑位移；不能为了常态动感破坏用户偏好。

## Do's and Don'ts

### Do:

- **Do** 延续自托管三字体、帆形标识与斜体 Zhouxu 字标。
- **Do** 将本文件的工作台 token 限定于工作台、相关门户菜单和弹层。
- **Do** 保留常态连续动效及减少动态效果回退。
- **Do** 让焦点、选中、禁用、处理中与失败状态同时具有真实语义和可见表达。
- **Do** 清楚区分已选资料、对话附件与回答引用。

### Don't:

- **Don't** 用虚构统计、仪表盘卡片或未实现能力填满研究工作台。
- **Don't** 将工作台字号、圆角或动效时长强制覆盖已认可的落地页和认证页。
- **Don't** 在工作台新增全屏动态背景或替换既有品牌素材。
- **Don't** 用图标字形、装饰性英文眉题或系统展示字体充当新组件语言。
- **Don't** 把资料选择高亮或上传成功显示成“已被引用”。
