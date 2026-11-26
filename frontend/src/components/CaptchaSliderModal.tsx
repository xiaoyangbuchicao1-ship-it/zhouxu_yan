import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronsRight, LoaderCircle, RotateCw, ShieldCheck, X } from "lucide-react";
import { requestCaptchaChallenge, verifyCaptcha, type CaptchaChallenge } from "@/lib/auth";
import "./captcha-slider.css";

interface CaptchaSliderModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (captchaToken: string) => void;
}

export default function CaptchaSliderModal({ open, onClose, onSuccess }: CaptchaSliderModalProps) {
  const [challenge, setChallenge] = useState<CaptchaChallenge | null>(null);
  const [loading, setLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [status, setStatus] = useState<"idle" | "dragging" | "success" | "failed">("idle");
  const [feedback, setFeedback] = useState<string>("");
  const [offsetX, setOffsetX] = useState(0);
  const [trackWidth, setTrackWidth] = useState(368);

  const dragStartRef = useRef<{ startX: number; startTime: number }>({ startX: 0, startTime: 0 });
  const trailRef = useRef<number[][]>([]);
  const offsetRef = useRef(0);
  const trackRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const knobRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<number | undefined>(undefined);
  const activeRef = useRef(false);
  const requestIdRef = useRef(0);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const maxSlide = challenge ? challenge.width - challenge.piece_width : 1;
  const travel = Math.max(1, trackWidth - 52);
  const knobX = (offsetX / maxSlide) * travel;

  const moveTo = (value: number) => {
    const next = Math.max(0, Math.min(value, maxSlide));
    offsetRef.current = next;
    setOffsetX(next);
    return next;
  };

  const loadChallenge = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    window.clearTimeout(timerRef.current);
    setLoading(true);
    setStatus("idle");
    setFeedback("");
    setOffsetX(0);
    offsetRef.current = 0;
    try {
      const data = await requestCaptchaChallenge();
      if (activeRef.current && requestId === requestIdRef.current) setChallenge(data);
    } catch {
      if (activeRef.current && requestId === requestIdRef.current) {
        setChallenge(null);
        setFeedback("拼图加载失败，请点击重试");
      }
    } finally {
      if (activeRef.current && requestId === requestIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      activeRef.current = true;
      returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      void loadChallenge();
    } else {
      activeRef.current = false;
      requestIdRef.current += 1;
      window.clearTimeout(timerRef.current);
      setChallenge(null);
      setOffsetX(0);
      setStatus("idle");
      setFeedback("");
      returnFocusRef.current?.focus();
    }
  }, [open, loadChallenge]);

  useEffect(() => {
    if (!open || !trackRef.current) return;
    const observer = new ResizeObserver(entries => setTrackWidth(entries[0].contentRect.width));
    observer.observe(trackRef.current);
    return () => observer.disconnect();
  }, [open, challenge]);

  useEffect(() => {
    if (open && challenge && !loading) knobRef.current?.focus();
  }, [open, challenge, loading]);

  const dismiss = () => {
    activeRef.current = false;
    requestIdRef.current += 1;
    window.clearTimeout(timerRef.current);
    onClose();
  };

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        activeRef.current = false;
        requestIdRef.current += 1;
        window.clearTimeout(timerRef.current);
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (loading || verifying || status === "success" || !challenge) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragStartRef.current = { startX: e.clientX, startTime: performance.now() };
    trailRef.current = [[0, 0, 0]];
    moveTo(0);
    setStatus("dragging");
    setFeedback("");
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (status !== "dragging") return;
    const delta = e.clientX - dragStartRef.current.startX;
    const clamped = moveTo((delta / travel) * maxSlide);

    const elapsed = Math.round(performance.now() - dragStartRef.current.startTime);
    trailRef.current.push([clamped, Math.round(e.clientY), elapsed]);
  };

  const verifyCurrent = async () => {
    if (status !== "dragging" || !challenge) return;
    const duration = Math.round(performance.now() - dragStartRef.current.startTime);
    const finalX = offsetRef.current;
    trailRef.current.push([Math.round(finalX), 0, duration]);

    setVerifying(true);
    const requestId = requestIdRef.current;
    try {
      const res = await verifyCaptcha({
        challenge_id: challenge.challenge_id,
        x: Math.round(finalX),
        duration_ms: duration,
        trail: trailRef.current,
      });
      if (!activeRef.current || requestId !== requestIdRef.current) return;

      if (res.verified && res.captcha_token) {
        setStatus("success");
        const sec = Math.max(0.2, duration / 1000).toFixed(1);
        setFeedback(`验证通过 · ${sec}s`);
        timerRef.current = window.setTimeout(() => {
          if (activeRef.current && requestId === requestIdRef.current) onSuccess(res.captcha_token!);
        }, 460);
      } else {
        setStatus("failed");
        setFeedback("拼图未对齐，请再试一次");
        timerRef.current = window.setTimeout(() => { if (activeRef.current) void loadChallenge(); }, 1000);
      }
    } catch {
      if (!activeRef.current || requestId !== requestIdRef.current) return;
      setStatus("failed");
      setFeedback("验证未完成，请再试一次");
      timerRef.current = window.setTimeout(() => { if (activeRef.current) void loadChallenge(); }, 1000);
    } finally {
      if (activeRef.current && requestId === requestIdRef.current) setVerifying(false);
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (status !== "dragging") return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    void verifyCurrent();
  };

  const handlePointerCancel = () => {
    if (status === "dragging") { moveTo(0); setStatus("idle"); }
  };

  const handleSliderKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!challenge || loading || verifying || status === "success") return;
    if (e.key === "Enter" || e.key === " ") {
      if (status === "dragging") { e.preventDefault(); void verifyCurrent(); }
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    if (status !== "dragging") {
      dragStartRef.current = { startX: 0, startTime: performance.now() };
      trailRef.current = [[0, 0, 0]];
      setStatus("dragging");
      setFeedback("");
    }
    const step = e.shiftKey ? 1 : 4;
    const next = moveTo(e.key === "Home" ? 0 : e.key === "End" ? maxSlide : offsetRef.current + (e.key === "ArrowRight" ? step : -step));
    trailRef.current.push([Math.round(next), 0, Math.round(performance.now() - dragStartRef.current.startTime)]);
  };

  const handleDialogKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !modalRef.current) return;
    const focusable = Array.from(modalRef.current.querySelectorAll<HTMLElement>("button:not(:disabled), [tabindex='0']"));
    if (e.shiftKey && document.activeElement === focusable[0]) {
      e.preventDefault();
      focusable[focusable.length - 1]?.focus();
    } else if (!e.shiftKey && document.activeElement === focusable[focusable.length - 1]) {
      e.preventDefault();
      focusable[0]?.focus();
    }
  };

  if (!open) return null;

  return (
    <div className="captcha-modal-backdrop" onClick={e => { if (e.target === e.currentTarget) dismiss(); }}>
      <div className={`captcha-modal-card ${status === "failed" ? "is-shake" : ""}`} ref={modalRef} role="dialog" aria-modal="true" aria-labelledby="captcha-title" aria-describedby="captcha-subtitle" onKeyDown={handleDialogKeyDown}>
        <header className="captcha-modal-header">
          <span className="captcha-modal-symbol"><ShieldCheck size={18} aria-hidden="true" /></span>
          <div className="captcha-modal-heading">
            <h2 id="captcha-title">安全验证</h2>
            <p id="captcha-subtitle">拖动滑块，将拼图对齐缺口</p>
          </div>
          <button type="button" className="captcha-modal-close" onClick={dismiss} aria-label="关闭验证">
            <X size={17} />
          </button>
        </header>

        <div
          className={`captcha-image-frame ${status === "success" ? "is-success" : ""}`}
          style={{ aspectRatio: challenge ? `${challenge.width} / ${challenge.height}` : "2 / 1" }}
        >
          {loading ? (
            <div className="captcha-loading-state">
              <LoaderCircle className="captcha-spin" size={22} />
              <span>正在生成拼图…</span>
            </div>
          ) : challenge ? (
            <div className="captcha-image-container">
              <img src={challenge.bg_image} alt="海图拼图背景，找到缺口" className="captcha-bg-img" draggable={false} />
              <img
                src={challenge.piece_image}
                alt="需要移动的拼图块"
                className={`captcha-piece-img ${status === "success" ? "is-success" : ""}`}
                style={{
                  top: `${challenge.y / challenge.height * 100}%`,
                  left: `${offsetX / challenge.width * 100}%`,
                  width: `${challenge.piece_width / challenge.width * 100}%`,
                  height: `${challenge.piece_height / challenge.height * 100}%`,
                }}
                draggable={false}
              />
              <button
                type="button"
                className="captcha-refresh-btn"
                onClick={loadChallenge}
                disabled={loading || verifying || status === "success"}
                title="换一张拼图"
                aria-label="换一张拼图"
              >
                <RotateCw size={15} />
              </button>
            </div>
          ) : (
            <div className="captcha-loading-state">
              <span>{feedback || "加载失败，请重试"}</span>
              <button type="button" className="captcha-retry-link" onClick={loadChallenge}>
                重新获取
              </button>
            </div>
          )}

          <div
            className={`captcha-status-banner ${status === "success" ? "is-success" : status === "failed" ? "is-error" : ""}`}
            role="status"
          >
            {status === "success" && <Check size={14} aria-hidden="true" />}
            <span>{feedback}</span>
          </div>
        </div>

        <div
          className={`captcha-track ${status === "success" ? "is-success" : status === "failed" ? "is-error" : ""} ${status === "dragging" ? "is-dragging" : ""}`}
          ref={trackRef}
        >
          <div
            className="captcha-track-progress"
            style={{ width: `${knobX + 50}px` }}
          />
          <span className={`captcha-track-tip ${status === "idle" ? "is-shimmer" : ""}`} aria-hidden="true">
            {status === "success" ? "验证通过" : verifying ? "核对中…" : status === "dragging" ? "松开完成验证" : "向右拖动滑块"}
          </span>
          <div
            className={`captcha-slider-knob ${status === "dragging" ? "is-active" : ""} ${status === "success" ? "is-success" : status === "failed" ? "is-error" : ""}`}
            ref={knobRef}
            style={{ transform: `translateX(${knobX}px)` }}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerCancel}
            onKeyDown={handleSliderKeyDown}
            tabIndex={challenge && !loading && !verifying ? 0 : -1}
            role="slider"
            aria-valuemin={0}
            aria-valuemax={maxSlide}
            aria-valuenow={offsetX}
            aria-label="移动拼图块"
            aria-describedby="captcha-keyboard-hint"
          >
            {verifying ? (
              <LoaderCircle className="captcha-spin" size={18} />
            ) : status === "success" ? (
              <Check size={18} />
            ) : (
              <ChevronsRight size={19} />
            )}
          </div>
        </div>
        <span className="captcha-sr-only" id="captcha-keyboard-hint">使用左右方向键移动，按回车键验证。</span>
      </div>
    </div>
  );
}
