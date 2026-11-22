import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/** Presentation buffer only. The canonical answer and stream cursor never wait for animation. */
export function useStreamingText(target: string, live: boolean) {
  const reduced = useReducedMotion();
  const [visible, setVisible] = useState(target);
  const latest = useRef(target);
  latest.current = target;
  useEffect(() => {
    if (!live || reduced) return;
    // Batch actual incoming text. No artificial character-by-character backlog.
    const timer = window.setInterval(() => setVisible(latest.current), 80);
    return () => window.clearInterval(timer);
  }, [reduced, live]);
  return !live || reduced || !target.startsWith(visible) ? target : visible;
}
