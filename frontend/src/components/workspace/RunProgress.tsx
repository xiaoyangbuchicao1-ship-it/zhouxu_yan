import { useEffect, useState } from "react";
import type { Activity, RunNodeState, RunStage } from "@/lib/workbench-api";
import { ChevronDown } from "lucide-react";
import { ActivityIndicator } from "./ActivityIndicator";
import "./RunProgress.css";
import { formatDuration } from "@/lib/format-duration";
import {
  isDetailCompleted,
  projectTimelineStages,
  readableHeaderLabel,
  readableStageDetail,
} from "@/lib/run-progress";

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

export function RunProgress({ activities = [], stages = [], nodes = [], live = false, status, elapsedMs, wallElapsedMs, observedAt, concise = false }: {
  activities?: Activity[]; stages?: RunStage[]; live?: boolean; status?: string;
  nodes?: RunNodeState[]; elapsedMs?: number | null; wallElapsedMs?: number | null; observedAt?: number; concise?: boolean;
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

  const renderedStages = projectTimelineStages(stages, activities, live).filter((stage) => {
    if (live || stage.id !== "delivery") return true;
    const concrete = stage.details.map(readableStageDetail).filter(d => d && d.replace(/^正在/, "") !== stage.label);
    return concrete.length > 0 || (stage.elapsed_ms ?? 0) >= 100;
  });
  const current = renderedStages.find(stage => stage.active) ?? renderedStages.at(-1);
  const currentNode = nodes.find(node => ["running", "preparing", "resuming", "retrying", "waiting_user"].includes(node.status)) ?? nodes.at(-1);
  const since = live && observedAt ? Math.max(0, now - observedAt) : 0;
  const total = live && stages.length && stages.every(s => s.elapsed_ms !== null)
    ? stages.reduce((sum, stage) => sum + stage.elapsed_ms!, 0) + since
    : elapsedMs ?? wallElapsedMs;
  const liveSteps = activities.filter(a => a.stage !== "connection" && a.id !== "connection" && a.label.trim());
  const latestActivity = liveSteps.at(-1);
  const connection = activities.at(-1)?.stage === "connection";
  const isWaiting = status === "waiting_user";
  const liveActionLabel = latestActivity?.label || current?.details.at(-1) || current?.label || currentNode?.label;
  const label = connection && live
    ? "正在恢复连接"
    : live
      ? readableHeaderLabel(liveActionLabel ?? "正在处理")
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

  const showNodeSteps = nodes.length > 1;
  const totalDetailCount = renderedStages.reduce((sum, stage) => sum + stage.details.length, 0);
  const stepCount = showNodeSteps
    ? nodes.length
    : Math.max(renderedStages.length, totalDetailCount);
  const canExpand = showNodeSteps ? nodes.length > 1 : renderedStages.length > 1 || totalDetailCount > 0;

  const summary = <>
        <span className={`ws-progress-status ${live ? "is-live" : status === "succeeded" ? "is-done" : ""}`} aria-hidden="true">
          {live ? <ActivityIndicator /> : <span className="ws-progress-status-dot" />}
        </span>
        <span className="ws-progress-label">{label}</span>
        {total != null && status !== "pausing" && <time>{formatDuration(total)}</time>}
        {canExpand && <span className="ws-progress-toggle" aria-label={open ? "收起步骤" : "查看步骤"}>{stepCount} 个步骤<ChevronDown size={13} aria-hidden="true" /></span>}
      </>;

  const details = canExpand && <div className="ws-progress-details">
        {showNodeSteps ? (
          <ol className="ws-progress-node-list" aria-label="模型运行步骤">
            {nodes.map(node => <li className={`ws-progress-node ws-progress-node-${node.status}`} key={node.node_id}>
              <span className="ws-progress-node-dot" aria-hidden="true" />
              <span className="ws-progress-node-title">{readableHeaderLabel(node.label)}</span>
              <span className="ws-progress-node-meta">{node.status === "succeeded" && node.duration_ms != null ? formatDuration(node.duration_ms) : nodeStatusLabel[node.status]}{node.status !== "succeeded" && node.duration_ms != null ? ` · ${formatDuration(node.duration_ms)}` : ""}</span>
              {node.error?.message && <span className="ws-progress-node-error">{node.error.message}</span>}
            </li>)}
          </ol>
        ) : (
          <ol className="ws-pipeline-list" aria-label="模型处理步骤">
            {renderedStages.map((stage) => {
              const stageRunning = live && stage.active;
              const stageDetails = stage.details
                .map(d => readableStageDetail(d))
                .filter(d => d && d.replace(/^正在/, "") !== stage.label);
              const timeText = stage.elapsed_ms === null
                ? (stageRunning ? "进行中" : "—")
                : live && !stage.active && stage.elapsed_ms === 0
                  ? "—"
                  : formatDuration(stage.elapsed_ms + (stageRunning ? since : 0));
              return (
                <li key={stage.id} className="ws-pipeline-stage" data-active={stageRunning}>
                  <span className="ws-pipeline-marker" aria-hidden="true" />
                  <div className="ws-pipeline-head">
                    <span className="ws-pipeline-title">{stage.label}</span>
                    <time className="ws-pipeline-time">{timeText}</time>
                  </div>
                  {stageDetails.length > 0 && (
                    <ul className="ws-pipeline-substeps">
                      {stageDetails.map((detail, idx) => {
                        const isLastRunning = stageRunning && idx === stageDetails.length - 1 && !isDetailCompleted(detail);
                        return (
                          <li key={`${stage.id}:${idx}`} className="ws-pipeline-substep" data-running={isLastRunning}>
                            <span className="ws-pipeline-subdot" aria-hidden="true" />
                            <span>{detail}</span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {elapsedMs != null && wallElapsedMs != null && wallElapsedMs > elapsedMs + 1000 && <p className="ws-progress-note">实际处理 {formatDuration(elapsedMs)}，等待 {formatDuration(wallElapsedMs - elapsedMs)}</p>}
      </div>;
  return <section className={`ws-progress ${concise ? "ws-progress-concise" : ""}`} data-testid="run-progress" aria-label="模型处理状态">
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
