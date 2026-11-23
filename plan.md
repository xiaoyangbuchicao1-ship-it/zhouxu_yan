# 舟序前端重构计划

批准日期：2026-11-24  
参考提案：`.ai-tmp/design-proposal/preview.html`  
约束：`docs/engineering-lessons.md`，`AGENTS.md`

---

## 总原则

- 后端、接口、数据库、对话运行逻辑（含暂停/继续）**一行不动**。
- `lib/*-api.ts`、hooks、auth 相关文件**不改动**。
- 冻结区：开场加载动画、首页展览（`Index.tsx`、`maritime.css` landing 部分）、`AuthVaultBackground.tsx` 及 `hexagonal-vault.tsx`**不改动**。
- 每个阶段必须完成**真实浏览器（127.0.0.1:18080）验收并截图**，通过后才进入下一阶段。
- 视觉验收必须使用真实截图，不能仅凭 DOM 或功能检查宣称完成。

---

## 设计决策（已批准）

| 项目 | 决定 |
|---|---|
| 管理台导航 | 六栏：总览 / 任务 / 账户 / 模型 / 技能 / 审计 |
| 用户中心统计方块 | 去掉 |
| 主按钮颜色 | 全站浅色（与登录页一致）|
| framesbase 参考 | App Shell 1、Settings Form 1、Monitoring 1 的设计语言 |

---

## 阶段 0 · 规格 + 两项立即生效的修复

**目标**：建立统一变量；完成两件与其他页面无关的独立修复。

### 0-A 扩展设计令牌

修改 `frontend/src/styles/tokens.css`，在现有 `--zx-*` 变量之后追加：

- **字号**：`--zx-t-xs/sm/base/md/lg/xl` = 12/13/14/16/20/28px
- **圆角**：`--zx-r-ctl/card/dlg/pill` = 6/10/14/999px
- **间距**：`--zx-sp-1~sp-12` = 4/8/12/16/24/32/40/48/64px
- **控件高**：`--zx-h-sm/md` = 32/36px
- **动效**：`--zx-dur-fast/base/slow` = 140/220/360ms（映射现有值），`--zx-ease` 已存在
- **层级**：`--zx-z-menu/dialog/toast` = 100/200/300
- **窗口**：`--zx-dlg-s/m/l` = 440/640/920px，`--zx-drawer` = 420px
- **面层**：`--zx-elev-1` = `0 2px 8px rgba(0,0,0,.35)`，`--zx-elev-2` = `0 8px 32px rgba(0,0,0,.5)`，`--zx-elev-3` = `0 24px 80px rgba(0,0,0,.6)`

### 0-B 去掉侧栏项目会话数量

修改 `frontend/src/components/workspace/Sidebar.tsx`：  
删除 `<span className="ws-count">{count}</span>` 及对 `count` 的无用引用。

### 0-C 管理员登录与用户登录统一

修改 `frontend/src/pages/AdminLogin.tsx`：  
使用 `auth.css` 的外壳（`auth-page`, `auth-background`, `auth-background-shade`, `auth-page-header`, `auth-stage`, `auth-panel`, `auth-page-footer`）和 `auth-submit`（浅色胶囊按钮）；表单字段改用 `auth-field`/`auth-fields`；添加与用户登录相同的页脚（"让事实有序，让判断有据。"）。  
从 `admin.css` 删除 `.admin-login-page`、`.admin-login-bg`、`.admin-login-header`、`.admin-login-panel`、`.admin-submit`、`.admin-login-*` 等不再使用的选择器。

### 0 阶段验收（5 条）

1. **登录背景一致**：用户登录和管理员登录截图叠加比对，背景亮度、六边形动效视觉上无差别。
2. **侧栏无数字**：工作台侧栏所有项目名称旁无数字角标。
3. **管理员登录可用**：用管理员账号完整走一次登录流程，进入 `/admin/overview` 成功。
4. **用户登录未破坏**：普通账号从 `/login` 完整登录，进入 `/workspace`，冻结区（首页动画、背景）截图与基准一致。
5. **TypeScript 编译无错**：`docker run --rm zx-fe-check sh -c "npm run typecheck"` 通过。

---

## 阶段 1 · 统一弹窗与用户中心

**目标**：三种窗口尺寸 + 一套过渡；用户中心按新结构重做。

- 添加共用弹窗样式（`styles/kit.css`）：S/M/L 三种宽度、统一进出动画（200ms in，140ms out）。
- `UserCenterDialog.tsx`：左侧栏目（账号 / 记忆 / 快捷指令 / 偏好与快捷键），右侧设置行（divider 分隔，参考 Settings Form 1 的 SettingsRow 模式），高度固定 640px，内容滚动。底部保存状态栏（"已保存 / 未保存更改"）。去掉 4 个统计方块。

### 1 阶段验收（3 条）

1. **窗口不跳**：在用户中心来回切换 4 个栏目，测量窗口宽高，始终不变。
2. **设置可保存**：每个栏目至少一项设置修改后能成功保存（调接口），刷新后值仍在。
3. **所有原有设置项都在**：与改动前对照，不能缺少任何设置选项。

---

## 阶段 2 · 管理台重构

**目标**：新外壳、六个导航栏目、各页面整理。

### 新路由

| 新路由 | 旧来源 | 接口 |
|---|---|---|
| `/admin/overview` | 现有（整理） | 现有 |
| `/admin/tasks` | 拆自 `/admin/operations` | `/admin/tasks` |
| `/admin/accounts` | 现有（加抽屉） | 现有 |
| `/admin/models` | 现有（整理） | 现有 |
| `/admin/skills` | 拆自 `/admin/operations` | `/admin/skills` |
| `/admin/audit` | 现有（整理为紧凑表格） | 现有 |

旧 `/admin/operations` 删除，`admin-navigation.ts` 更新。

### 2 阶段验收（3 条）

1. **管理员能办事**：用管理员账号改一个普通账号的能力权限、保存，刷新后权限仍在。
2. **审计有记录**：上一步的操作在 `/admin/audit` 能查到。
3. **所有原有功能都在**：模型测试连通性、账户停用、技能开放/关闭——每项操作至少能触发一次，结果与后端一致。

---

## 阶段 3 · 工作台页面整理

**目标**：技能、资料库、侧栏、对话页用统一变量替换硬编码值；去掉口号和重复元素。

- **技能页**：每行整行可点，去掉重复图标和第二个链接，右侧只留一个模式标签。
- **资料库**：去掉"依据先于结论""回到真实的海事资料"口号，标题一行，搜索框加边框，空状态只说"输入关键词开始检索"。
- **对话页**：用 `--zx-*` 变量替换硬编码颜色，不改逻辑。

### 3 阶段验收（4 条）

1. **对话没坏**：普通账号用真实 DeepSeek 完整跑一轮"提问 → 暂停 → 继续 → 完成"，表现与阶段 0 前一致。
2. **资料库能搜索**：输入法规名称，搜到结果。
3. **技能可使用**：点技能行进入对应模式，不报错。
4. **侧栏无口号**：工作台内页面无对外宣传性文案。

---

## 阶段 4 · 收尾

**目标**：清理旧代码，全量截图验收。

- 删除所有 `ws-uc-*`、旧 `admin-login-*` 等迁移后不再使用的 CSS 选择器。
- 删除未使用的 CSS 变量和 Tailwind 任意值。
- 五种屏幕宽度全量截图：1440 / 1024 / 900 / 390 / 320。
- 更新 `docs/engineering-lessons.md`（如本轮发现新约束）。

### 4 阶段验收（3 条）

1. **旧类名清零**：`grep -r "ws-uc-\|admin-login-\|md-result " frontend/src --include="*.tsx" --include="*.css"` 无结果。
2. **TypeScript + lint 全通过**：`typecheck && lint` 无错误无警告。
3. **验收报告**：用大白话写清楚测了什么、在哪里测的、结果是什么；未运行的项目明确写"未运行"。

---

## 时序约束

- 每阶段最后一条验收未通过，不进入下一阶段，不标记完成。
- 阶段 0 的修复（0-B、0-C）不依赖后续阶段，可以先上线。
- 阶段 2 的新路由不保留 `/admin/operations` 重定向（项目规则：不添加无用兼容层）。
- 临时脚本和截图放在 `D:\projects\codex\论文库\.ai-tmp\`，不进入 git。
