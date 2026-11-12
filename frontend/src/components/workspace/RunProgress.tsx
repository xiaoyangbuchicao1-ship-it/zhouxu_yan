import type { Activity } from "@/lib/workbench-api";
import { ChevronDown, Check } from "lucide-react";
import { ActivityIndicator } from "./ActivityIndicator";
import "./RunProgress.css";

export function RunProgress({ activities = [], live = false }: { activities?: Activity[]; live?: boolean }) {
  const latest = activities.at(-1);
  if (!live && !activities.length) return null;
  return <div className="ws-progress" data-testid="run-progress">
    {live && <div className="ws-progress-current" role="status" aria-live="polite">
      <ActivityIndicator />
      <span className="ws-progress-label">{latest?.label || "正在接收任务"}</span>
    </div>}
    {activities.length > 0 && <details className="ws-activities">
      <summary>{!live && <Check size={14} />}<span>处理过程</span><ChevronDown size={14} /></summary>
      <ol>{activities.map(a => <li key={a.id}><span>{a.label}</span>{a.detail && <p>{a.detail}</p>}</li>)}</ol>
    </details>}
  </div>;
}
