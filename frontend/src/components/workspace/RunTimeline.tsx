import type { RunNodeState } from "@/lib/workbench-api";
import { formatDuration } from "@/lib/format-duration";

interface Props {
  nodes: RunNodeState[];
  activeElapsedMs?: number | null;
  wallElapsedMs?: number | null;
}

const kindLabel: Record<RunNodeState["kind"], string> = {
  skill: "Skill",
  tool: "工具",
  model: "模型",
  file: "文件",
  human_gate: "确认",
  compile: "编译",
  subgraph: "子任务",
  transform: "转换",
};

const statusLabel: Record<RunNodeState["status"], string> = {
  queued: "排队中",
  preparing: "准备中",
  running: "运行中",
  waiting_user: "等待输入",
  pausing: "暂停中",
  paused: "已暂停",
  resuming: "恢复中",
  retrying: "重试中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function RunTimeline({ nodes, activeElapsedMs, wallElapsedMs }: Props) {
  if (!nodes.length) return null;
  const elapsed = activeElapsedMs ?? wallElapsedMs;
  return (
    <details className="ws-node-details"><summary>执行步骤 · {nodes.length}</summary><section className="ws-run-timeline" aria-label="运行节点时间线">
      <div className="ws-run-timeline-heading">
        <span>运行路径</span>
        {elapsed != null && <time>已运行 {formatDuration(elapsed)}</time>}
      </div>
      <ol className="ws-run-timeline-list">
        {nodes.map((node) => (
          <li className={`ws-run-node ws-run-node-${node.status}`} key={node.node_id}>
            <span className="ws-run-node-dot" aria-hidden="true" />
            <div className="ws-run-node-copy">
              <div className="ws-run-node-title">
                <span>{node.label}</span>
                <span className="ws-run-node-kind">{kindLabel[node.kind]}</span>
              </div>
              <div className="ws-run-node-meta">
                <span>{statusLabel[node.status]}</span>
                {node.duration_ms != null && <time>{formatDuration(node.duration_ms)}</time>}
                {node.attempt > 1 && <span>第 {node.attempt} 次尝试</span>}
              </div>
              {node.error?.message && <p className="ws-run-node-error">{node.error.message}</p>}
            </div>
          </li>
        ))}
      </ol>
      {activeElapsedMs != null && wallElapsedMs != null && wallElapsedMs > activeElapsedMs + 1000 && (
        <p className="ws-run-timeline-note">已执行 {formatDuration(activeElapsedMs)}，等待或暂停 {formatDuration(wallElapsedMs - activeElapsedMs)}</p>
      )}
    </section></details>
  );
}

