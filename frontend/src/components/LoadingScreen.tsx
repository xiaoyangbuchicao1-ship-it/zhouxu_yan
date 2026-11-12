import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

const WORDS = ["Research", "Reason", "Resolve"];
const DURATION = 2700; // ms
const WORD_INTERVAL = 900;

interface LoadingScreenProps {
  onComplete: () => void;
}

const LoadingScreen = ({ onComplete }: LoadingScreenProps) => {
  const [count, setCount] = useState(0);
  const [wordIndex, setWordIndex] = useState(0);
  const startRef = useRef<number | null>(null);
  const doneRef = useRef(false);

  const animate = useCallback(
    (ts: number) => {
      if (!startRef.current) startRef.current = ts;
      const elapsed = ts - startRef.current;
      const progress = Math.min(elapsed / DURATION, 1);
      setCount(Math.floor(progress * 100));

      if (progress < 1) {
        requestAnimationFrame(animate);
      } else if (!doneRef.current) {
        doneRef.current = true;
        setTimeout(onComplete, 400);
      }
    },
    [onComplete]
  );

  useEffect(() => {
    requestAnimationFrame(animate);
  }, [animate]);

  useEffect(() => {
    const interval = setInterval(() => {
      setWordIndex((i) => (i + 1) % WORDS.length);
    }, WORD_INTERVAL);
    return () => clearInterval(interval);
  }, []);

  const easing = [0.4, 0, 0.2, 1] as const;

  return (
    <motion.div
      className="fixed inset-0 z-[9999] bg-bg flex flex-col"
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}
    >
      {/* Top-left: Portfolio */}
      <motion.span
        className="absolute top-8 left-8 md:top-12 md:left-12 text-xs md:text-sm text-muted uppercase tracking-[0.3em]"
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.1 }}
      >
        ZHOUXU · 舟序
      </motion.span>

      {/* Center: Rotating words */}
      <div className="flex-1 flex items-center justify-center">
        <AnimatePresence mode="wait">
          <motion.span
            key={wordIndex}
            className="text-4xl md:text-6xl lg:text-7xl font-display italic text-text-primary/80"
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -20, opacity: 0 }}
            transition={{ duration: 0.4, ease: [...easing] }}
          >
            {WORDS[wordIndex]}
          </motion.span>
        </AnimatePresence>
      </div>

      {/* Bottom-right: Counter */}
      <motion.span
        className="absolute bottom-8 right-8 md:bottom-12 md:right-12 text-6xl md:text-8xl lg:text-9xl font-display text-text-primary tabular-nums"
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.1 }}
      >
        {String(count).padStart(3, "0")}
      </motion.span>

      {/* Progress bar */}
      <div className="absolute bottom-0 inset-x-0 h-[3px] bg-stroke/50">
        <div
          className="h-full accent-gradient origin-left"
          style={{
            transform: `scaleX(${count / 100})`,
            boxShadow: "0 0 8px rgba(137, 170, 204, 0.35)",
            transition: "transform 0.05s linear",
          }}
        />
      </div>
    </motion.div>
  );
};

export default LoadingScreen;
