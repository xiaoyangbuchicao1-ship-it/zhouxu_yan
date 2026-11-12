import type { CSSProperties } from "react";
import "./ActivityIndicator.css";

/** Essential activity feedback; its caller owns the accessible status text. */
export function ActivityIndicator({ size = 14 }: { size?: number }) {
  return <span className="ws-activity-indicator" data-motion="activity" aria-hidden="true"
    style={{ "--activity-size": `${size}px` } as CSSProperties}>
    <span className="ws-activity-dot" data-motion="activity" />
    <span className="ws-activity-dot" data-motion="activity" />
    <span className="ws-activity-dot" data-motion="activity" />
  </span>;
}
