import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/** Presentation buffer only. The canonical answer and stream cursor never wait for animation. */
export function useStreamingText(target: string, live: boolean) {
  const reduced = useReducedMotion();
  const [visible, setVisible] = useState(target);
  const shown = useRef(target);
  useEffect(() => {
    if (reduced || !target.startsWith(shown.current)) {
      shown.current = target; setVisible(target); return;
    }
    let frame = 0;
    const advance = () => {
      const backlog = Array.from(target.slice(shown.current.length));
      if (!backlog.length) return;
      // A small stream reveals individual characters; bursts catch up in <=24 frames.
      const take = Math.max(1, Math.ceil(backlog.length / 24));
      shown.current += backlog.slice(0, take).join("");
      setVisible(shown.current);
      if (shown.current !== target) frame = requestAnimationFrame(advance);
    };
    frame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(frame);
  }, [target, reduced, live]);
  return visible;
}
