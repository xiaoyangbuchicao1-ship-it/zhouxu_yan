import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const readSrc = (rel) => fs.readFileSync(path.join(FRONTEND_DIR, "src", rel), "utf-8");

test("CommandPalette 指向 /workspace/library，不再包含死链 /workspace/resources", () => {
  const src = readSrc("components/workspace/CommandPalette.tsx");
  assert.ok(!src.includes("/workspace/resources"));
  assert.ok(src.includes("/workspace/library"));
});

test("Sidebar 将项目会话归纳在项目折叠树下，置顶与最近只展示非项目会话", () => {
  const src = readSrc("components/workspace/Sidebar.tsx");
  assert.ok(src.includes("c.project_id === project.id"));
  assert.ok(src.includes("!c.project_id"));
  assert.ok(src.includes("ws-project-branch"));
});

test("Conversation 欢迎区聚焦四张核心卡片并按聊天/工作分流", () => {
  const src = readSrc("components/workspace/Conversation.tsx");
  for (const label of ["海事条文检索", "海事案例检索", "海事论文检索", "海事论文写作"]) {
    assert.ok(src.includes(label), `missing welcome card ${label}`);
  }
  assert.ok(src.includes('skillId: "maritime-paper-research"'));
  assert.ok(src.includes('skillId: "csl-law-paper-generator"'));
  assert.ok(src.includes('mode: "chat"'));
  assert.ok(src.includes('mode: "work"'));
});

test("Skills 卡片使用干净名称与标签，按 mode_scope 跳转对应模式", () => {
  const src = readSrc("components/workspace/Skills.tsx");
  assert.ok(src.includes("在聊天模式使用"));
  assert.ok(src.includes("在工作模式使用"));
  assert.ok(src.includes("mode=${targetMode}"));
  assert.ok(!src.includes("（工作包）"));
});

import { mergeProgressActivity, projectTimelineStages } from "../src/lib/run-progress.ts";

test("use-workspace 使用后端目录返回的 default_profile_id 作为默认模型", () => {
  const src = readSrc("hooks/use-workspace.ts");
  assert.ok(src.includes("modelCatalog.data?.default_profile_id"));
  assert.ok(!src.includes('|| "deepseek-flash"'));
  assert.ok(src.includes("mergeProgressActivity(c.activities"));
});

test("mergeProgressActivity 保留多步流水线并用完成回执原地替换进行中步骤", () => {
  let acts = [];
  acts = mergeProgressActivity(acts, { id: "r1:model_processing", stage: "model_processing", status: "running", label: "理解问题并规划步骤" }, 1);
  acts = mergeProgressActivity(acts, { id: "r1:skill_reading", stage: "skill_reading", status: "running", label: "加载技能 · 海事法学论文写作" }, 2);
  acts = mergeProgressActivity(acts, { id: "r1:skill_reading", stage: "skill_reading", status: "running", label: "已加载技能 · 海事法学论文写作" }, 3);
  acts = mergeProgressActivity(acts, { id: "r1:law_searching", stage: "law_searching", status: "running", label: "检索法规 · 海商法" }, 4);
  acts = mergeProgressActivity(acts, { id: "r1:law_searching", stage: "law_searching", status: "running", label: "检索法规 · 海商法（命中 6 条）" }, 5);
  acts = mergeProgressActivity(acts, { id: "r1:law_searching", stage: "law_searching", status: "running", label: "检索法规 · 海商法（命中 10 条）" }, 6);
  acts = mergeProgressActivity(acts, { id: "r1:synthesizing", stage: "synthesizing", status: "running", label: "分析已查依据并继续（第 1 轮）" }, 7);

  assert.equal(acts.length, 5);
  assert.deepEqual(
    acts.map((a) => a.label),
    ["理解问题并规划步骤", "已加载技能 · 海事法学论文写作", "检索法规 · 海商法（命中 6 条）", "检索法规 · 海商法（命中 10 条）", "分析已查依据并继续（第 1 轮）"],
  );
  assert.equal(acts[0].status, "done");
  assert.equal(acts[2].status, "done");
  assert.equal(acts[4].status, "running");

  const stages = projectTimelineStages(
    [{ id: "model", label: "模型处理中", elapsed_ms: 0, active: true, details: [] }],
    acts,
    true,
  );
  const byId = Object.fromEntries(stages.map((s) => [s.id, s]));
  assert.equal(byId.model.label, "理解与规划");
  assert.equal(byId.model.active, false);
  assert.equal(byId.synthesis.active, true);
  assert.deepEqual(byId.research.details, ["检索法规 · 海商法（命中 6 条）", "检索法规 · 海商法（命中 10 条）"]);
});

test("RunProgress 展示阶段子步骤清单，Composer 技能条与 Sidebar 账户角标去除了双重边框与括号", () => {
  const runProgress = readSrc("components/workspace/RunProgress.tsx");
  assert.ok(runProgress.includes("ws-pipeline-list"));
  assert.ok(runProgress.includes("ws-pipeline-substep"));
  assert.ok(runProgress.includes("const showNodeSteps = nodes.length > 1;"));

  const sidebar = readSrc("components/workspace/Sidebar.tsx");
  assert.ok(!sidebar.includes("（内测中）"));
  assert.ok(!sidebar.includes("ws-beta-badge"));
  assert.ok(sidebar.includes("ws-account-subline"));
  assert.ok(sidebar.includes("内测版"));

  const conversation = readSrc("components/workspace/Conversation.tsx");
  assert.ok(conversation.includes("ws-context-divider"));
  assert.ok(conversation.includes("插入示例提问"));

  const css = readSrc("pages/workspace.css");
  assert.ok(css.includes(".ws-context-fill"));
  assert.ok(css.includes("border: none;"));
});

test("资料与依据按 全部/网络资料/法规/案例/论文资料 排序，支持网络外链及引用区分", () => {
  const panel = readSrc("components/workspace/ResearchPanel.tsx");
  assert.ok(panel.includes('["all", "web", "law", "case", "paper"]'));
  assert.ok(panel.includes('target="_blank"'));
  assert.ok(panel.includes('rel="noopener noreferrer"'));
  assert.ok(panel.includes("回答已引用"));
  assert.ok(panel.includes("其他检索参考"));
  assert.ok(panel.includes("used_in_answer"));

  const adminModels = readSrc("pages/admin/AdminModelsPage.tsx");
  assert.ok(adminModels.includes("国内主流模型设为全局默认"));
  assert.ok(adminModels.includes("全站默认"));

  const adminAccounts = readSrc("pages/admin/AdminAccountsPage.tsx");
  assert.ok(adminAccounts.includes("权限分组"));
  assert.ok(adminAccounts.includes("当前生效可用模型"));
  assert.ok(adminAccounts.includes("我的备注别名"));

  const adminCss = readSrc("pages/admin.css");
  assert.ok(adminCss.includes(".admin-spin { animation:"));

  const authTsx = readSrc("pages/Auth.tsx");
  assert.ok(!authTsx.includes("auth-mode-nav"));

  const settingsModal = readSrc("components/workspace/UserCenterDialog.tsx");
  assert.ok(settingsModal.includes("回复完成提示音"));
  assert.ok(settingsModal.includes("playCompletionSound"));

  const sidebarTsx = readSrc("components/workspace/Sidebar.tsx");
  assert.ok(sidebarTsx.includes("ws-conv-spinner"));
  assert.ok(sidebarTsx.includes("ws-conv-dot"));

  const wbApi = readSrc("lib/workbench-api.ts");
  assert.ok(wbApi.includes('message.includes("余额不足")'));
  assert.ok(wbApi.includes('message.includes("额度不足")'));
});
