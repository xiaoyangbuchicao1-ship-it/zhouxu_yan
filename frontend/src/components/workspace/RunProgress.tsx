import { useEffect, useState } from "react";
import type { Activity, RunNodeState, RunStage } from "@/lib/workbench-api";
import { ChevronDown } from "lucide-react";
import { ActivityIndicator } from "./ActivityIndicator";
import "./RunProgress.css";
import { formatDuration } from "@/lib/format-duration";

const nodeStatusLabel: Record<RunNodeState["status"], string> = {
  queued: "排队中",
  preparing: "准备中",
  running: "进行中",
  waiting_user: "等待确认",
  pausing: "暂停中",
  paused: "已暂停",
  resuming: "恢复中",
  retrying: "重试中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const stageLabel: Record<string, string> = {
  "模型处理中": "模型处理中",
  "整理结果": "整理结果",
  "执行任务": "执行任务",
  "查找资料": "查找资料",
  "撰写回答": "组织回答",
  "生成文件": "生成文件",
};

function readableDetail(value: string) {
  return value
    .replace(/正在装配上下文/g, "模型处理中")
    .replace(/正在整理上下文/g, "模型处理中")
    .replace(/正在检查上下文/g, "核对结果")
    .replace(/正在生成回答/g, "组织回答");
}

function readableLabel(value: string) {
  return value
    .replace(/装配上下文/g, "模型处理中")
    .replace(/整理上下文/g, "模型处理中")
    .replace(/检查上下文/g, "核对结果")
    .replace(/生成回答/g, "组织回答");
}

export function RunProgress({ activities = [], stages = [], nodes = [], live = false, status, elapsedMs, wallElapsedMs, observedAt }: {
  activities?: Activity[]; stages?: RunStage[]; live?: boolean; status?: string;
  nodes?: RunNodeState[]; elapsedMs?: number | null; wallElapsedMs?: number | null; observedAt?: number;
}) {
  const [now, setNow] = useState(Date.now);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [live]);
  if (!live && !stages.length && !nodes.length && elapsedMs == null) return null;
  const current = stages.find(stage => stage.active) ?? stages.at(-1);
  const currentNode = nodes.find(node => ["running", "preparing", "resuming", "retrying", "waiting_user"].includes(node.status)) ?? nodes.at(-1);
  const since = live && observedAt ? Math.max(0, now - observedAt) : 0;
  const total = live && stages.length && stages.every(s => s.elapsed_ms !== null)
    ? stages.reduce((sum, stage) => sum + stage.elapsed_ms!, 0) + since
    : elapsedMs ?? wallElapsedMs;
  const connection = activities.at(-1)?.stage === "connection";
  const isWaiting = status === "waiting_user";
  const label = connection && live
    ? "正在恢复连接"
    : live
      ? readableLabel(currentNode?.label ?? stageLabel[current?.label ?? ""] ?? current?.label ?? "正在处理")
      : status === "paused"
        ? "已暂停"
        : status === "pausing"
          ? "正在暂停"
          : isWaiting
            ? "等待你确认"
            : status === "failed"
              ? "处理未完成"
              : status === "cancelled"
                ? "已取消"
                : "已完成";
  // A single model node is the normal path for a short greeting. Keep that
  // path quiet; only expose the disclosure when the run has real stages.
  const showNodeSteps = nodes.length > 1 || stages.length <= 1;
  const stepCount = showNodeSteps ? nodes.length : stages.length;
  const canExpand = stepCount > 1;
  const renderedStages = stages.map(stage => ({ ...stage, label: stageLabel[stage.label] ?? stage.label }));
  const summary = <>
        <span className={`ws-progress-status ${live ? "is-live" : status === "succeeded" ? "is-done" : ""}`} aria-hidden="true">
          {live ? <ActivityIndicator /> : <span className="ws-progress-status-dot" />}
        </span>
        <span className="ws-progress-label">{label}</span>
        {total != null && <time>{formatDuration(total)}</time>}
        {canExpand && <span className="ws-progress-toggle" aria-label={open ? "收起步骤" : "查看步骤"}>{stepCount} 个步骤<ChevronDown size={13} aria-hidden="true" /></span>}
      </>;
  const details = canExpand && <div className="ws-progress-details">
        {showNodeSteps ? (
          <ol className="ws-progress-node-list" aria-label="模型运行步骤">
            {nodes.map(node => <li className={`ws-progress-node ws-progress-node-${node.status}`} key={node.node_id}>
              <span className="ws-progress-node-dot" aria-hidden="true" />
              <span className="ws-progress-node-title">{readableLabel(node.label)}</span>
              <span className="ws-progress-node-meta">{node.status === "succeeded" && node.duration_ms != null ? formatDuration(node.duration_ms) : nodeStatusLabel[node.status]}{node.status !== "succeeded" && node.duration_ms != null ? ` · ${formatDuration(node.duration_ms)}` : ""}</span>
              {node.error?.message && <span className="ws-progress-node-error">{node.error.message}</span>}
            </li>)}
          </ol>
        ) : (
          <ol className="ws-stage-list" aria-label="模型处理步骤">
            {renderedStages.map(stage => <li key={stage.id} data-active={live && stage.active}>
              <span>{stage.label}</span>
              <time>{stage.elapsed_ms === null ? "—" : formatDuration(stage.elapsed_ms + (live && stage.active ? since : 0))}</time>
              {stage.details.at(-1) && readableDetail(stage.details.at(-1)!).replace(/^正在/, "") !== stage.label && <small>{readableDetail(stage.details.at(-1)!)}</small>}
            </li>)}
          </ol>
        )}
        {elapsedMs != null && wallElapsedMs != null && wallElapsedMs > elapsedMs + 1000 && <p className="ws-progress-note">实际处理 {formatDuration(elapsedMs)}，等待 {formatDuration(wallElapsedMs - elapsedMs)}</p>}
      </div>;
  return <section className="ws-progress" data-testid="run-progress" aria-label="模型处理状态">
    {canExpand ? (
      <details className="ws-stage-details" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>{summary}</summary>
        {details}
      </details>
    ) : (
      <div className="ws-stage-details ws-stage-details-static">{summary}</div>
    )}
  </section>;
}
