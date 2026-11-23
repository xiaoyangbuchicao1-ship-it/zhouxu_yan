import type { Activity, RunStage } from "@/lib/workbench-api";

export const DISPLAY_STAGE_LABEL: Record<string, string> = {
  "模型处理中": "理解与规划",
  "加载技能": "加载技能",
  "查找资料": "查找资料",
  "综合分析与推进": "综合分析",
  "生成文件": "生成文件",
  "执行任务": "执行任务",
  "撰写回答": "组织回答",
  "整理结果": "整理结果",
};

const RAW_STAGE_BUCKET: Record<string, { id: string; label: string }> = {
  skill_reading: { id: "skills", label: "加载技能" },
  material_parsing: { id: "research", label: "查找资料" },
  material_reading: { id: "research", label: "查找资料" },
  material_searching: { id: "research", label: "查找资料" },
  case_searching: { id: "research", label: "查找资料" },
  case_reading: { id: "research", label: "查找资料" },
  law_searching: { id: "research", label: "查找资料" },
  paper_searching: { id: "research", label: "查找资料" },
  paper_reading: { id: "research", label: "查找资料" },
  web_searching: { id: "research", label: "查找资料" },
  source_reading: { id: "research", label: "查找资料" },
  researching: { id: "research", label: "查找资料" },
  work_package: { id: "files", label: "生成文件" },
  file_writing: { id: "files", label: "生成文件" },
  file_compiling: { id: "files", label: "生成文件" },
  calculating: { id: "execution", label: "执行任务" },
  execution: { id: "execution", label: "执行任务" },
  action: { id: "execution", label: "执行任务" },
  writing: { id: "writing", label: "撰写回答" },
  synthesizing: { id: "synthesis", label: "综合分析与推进" },
  memory: { id: "model", label: "模型处理中" },
  perception: { id: "model", label: "模型处理中" },
  reasoning: { id: "model", label: "模型处理中" },
  reflection: { id: "model", label: "模型处理中" },
  model_processing: { id: "model", label: "模型处理中" },
  context_compaction: { id: "model", label: "模型处理中" },
  context_compaction_done: { id: "model", label: "模型处理中" },
  finalizing: { id: "delivery", label: "整理结果" },
};

const PENDING_TO_DONE: ReadonlyArray<readonly [string, string]> = [
  ["加载技能", "已加载技能"],
  ["撰写文稿", "已保存文稿"],
  ["修改文稿", "已修改文稿"],
  ["续写文稿", "已保存文稿"],
  ["编译生成", "已生成"],
  ["精读论文", "已读取"],
  ["核对案例", "已核对"],
  ["读取来源正文", "已读取来源正文"],
  ["读取上传材料", "已读取"],
  ["测算海事索赔", "已完成"],
  ["生成结构化", "已保存工作包"],
  ["核验 ", "已完成"],
  ["检查", "已完成"],
];

export function readableStageDetail(value: string): string {
  return value
    .replace(/正在装配上下文/g, "理解问题并规划步骤")
    .replace(/正在整理上下文/g, "理解问题并规划步骤")
    .replace(/正在检查上下文/g, "核对结果")
    .replace(/正在生成回答/g, "组织回答")
    .trim();
}

export function readableHeaderLabel(value: string): string {
  return value
    .replace(/装配上下文/g, "理解并规划")
    .replace(/整理上下文/g, "理解并规划")
    .replace(/检查上下文/g, "核对结果")
    .replace(/生成回答/g, "组织回答")
    .trim();
}

export function isDetailCompleted(label: string): boolean {
  const clean = label.trim();
  return clean.startsWith("已") || clean.includes("（命中") || clean.includes("（共") || clean.includes("（已完成");
}

function replacesPendingDetail(prev: string, next: string): boolean {
  if (!prev || !next) return false;
  if (prev === next) return true;
  if (isDetailCompleted(prev)) return false;
  const basePrev = prev.split("（", 1)[0];
  const baseNext = next.split("（", 1)[0];
  if (!prev.includes("（") && baseNext === basePrev && next.includes("（")) return true;
  return PENDING_TO_DONE.some(([pPrefix, dPrefix]) => prev.startsWith(pPrefix) && next.startsWith(dPrefix));
}

export function appendStageDetail(details: string[], nextLabel: string): string[] {
  const clean = readableStageDetail(nextLabel);
  if (!clean) return details;
  if (details.length > 0 && replacesPendingDetail(details[details.length - 1], clean)) {
    const copy = details.slice(0, -1);
    if (!copy.includes(clean)) copy.push(clean);
    return copy;
  }
  if (details.includes(clean)) return details;
  return [...details, clean];
}

export function mergeProgressActivity(activities: Activity[], next: Activity, sequence: number): Activity[] {
  if (next.id === "connection" || next.stage === "connection") {
    return [...activities.filter((a) => a.id !== next.id && a.stage !== "connection"), next];
  }
  const cleanLabel = readableStageDetail(next.label);
  const connectionItems = activities.filter((a) => a.stage === "connection" || a.id === "connection");
  const steps = activities.filter((a) => a.stage !== "connection" && a.id !== "connection");

  if (steps.length > 0) {
    const last = steps[steps.length - 1];
    if (last.stage === next.stage && replacesPendingDetail(last.label, cleanLabel)) {
      const updatedLast: Activity = {
        ...last,
        ...next,
        id: last.id,
        label: cleanLabel || last.label,
        status: isDetailCompleted(cleanLabel || last.label) ? "done" : next.status || "running",
      };
      return [...steps.slice(0, -1), updatedLast, ...connectionItems];
    }
  }

  const settled = steps.map((item) => (item.status === "running" ? { ...item, status: "done" } : item));
  const entry: Activity = {
    ...next,
    id: `${next.id}:${sequence}`,
    label: cleanLabel || next.label,
    status: isDetailCompleted(cleanLabel) ? "done" : next.status || "running",
  };
  return [...settled, entry, ...connectionItems];
}

export function displayStageLabel(rawLabel: string): string {
  return DISPLAY_STAGE_LABEL[rawLabel] ?? rawLabel;
}

export function projectTimelineStages(stages: RunStage[], activities: Activity[], live: boolean): RunStage[] {
  const cloned: RunStage[] = stages.map((stage) => ({
    ...stage,
    label: displayStageLabel(stage.label),
    details: stage.details.reduce<string[]>((acc, item) => appendStageDetail(acc, item), []),
  }));

  const liveSteps = activities.filter((a) => a.stage !== "connection" && a.id !== "connection" && a.label.trim());
  if (!live || liveSteps.length === 0) return cloned;

  const byId = new Map<string, RunStage>();
  const ordered: RunStage[] = [];
  for (const stage of cloned) {
    byId.set(stage.id, stage);
    ordered.push(stage);
  }

  let activeBucketId: string | null = null;
  for (const step of liveSteps) {
    const bucket = RAW_STAGE_BUCKET[step.stage] ?? { id: "model", label: "模型处理中" };
    activeBucketId = bucket.id;
    let target = byId.get(bucket.id);
    if (!target) {
      target = {
        id: bucket.id,
        label: displayStageLabel(bucket.label),
        elapsed_ms: null,
        active: false,
        details: [],
      };
      byId.set(bucket.id, target);
      ordered.push(target);
    }
    target.details = appendStageDetail(target.details, step.label);
  }

  if (activeBucketId) {
    for (const stage of ordered) {
      stage.active = stage.id === activeBucketId;
    }
  }
  return ordered;
}
