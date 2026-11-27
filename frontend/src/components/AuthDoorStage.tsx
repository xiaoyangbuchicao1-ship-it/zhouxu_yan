import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { Link, Outlet, useLocation, useNavigate, type NavigateOptions } from "react-router-dom";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import "@/pages/auth.css";

export type AuthRealm = "user" | "admin";

interface AuthDoorContextValue {
  realm: AuthRealm;
  isOpen: boolean;
  isTransitioning: boolean;
  toggleDoors: () => void;
  switchRealm: (target: AuthRealm) => void;
  enterPortal: (to: string, options?: NavigateOptions) => void;
}

const AuthDoorContext = createContext<AuthDoorContextValue | null>(null);

export function useAuthDoor(): AuthDoorContextValue {
  const ctx = useContext(AuthDoorContext);
  if (!ctx) {
    throw new Error("useAuthDoor must be used inside AuthDoorLayout");
  }
  return ctx;
}

/* Exact cubic-bezier evaluator for physical door motion (anticipation, swing, slam overshoot). */
function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const sampleX = (u: number) => ((3 * x1 - 3 * x2 + 1) * u + (3 * x2 - 6 * x1)) * u * u + 3 * x1 * u;
  const sampleY = (u: number) => ((3 * y1 - 3 * y2 + 1) * u + (3 * y2 - 6 * y1)) * u * u + 3 * y1 * u;
  const sampleDX = (u: number) => (3 * (3 * x1 - 3 * x2 + 1) * u + 2 * (3 * x2 - 6 * x1)) * u + 3 * x1;
  return (t: number) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    let u = t;
    for (let i = 0; i < 6; i++) {
      const err = sampleX(u) - t;
      const d = sampleDX(u);
      if (Math.abs(err) < 1e-5 || Math.abs(d) < 1e-6) break;
      u = Math.min(1, Math.max(0, u - err / d));
    }
    return sampleY(u);
  };
}

const EASE = {
  press: cubicBezier(0.45, 0, 0.55, 1),
  open: cubicBezier(0.22, 0.8, 0.25, 1),
  closeIn: cubicBezier(0.55, 0.05, 0.85, 0.4),
  spring: cubicBezier(0.2, 0.7, 0.3, 1),
  fade: cubicBezier(0.2, 0.7, 0.2, 1),
};

function computePerspective(vh: number): number {
  const fovRad = (11 * Math.PI) / 180;
  return Math.max(1200, Math.round(vh / 2 / Math.tan(fovRad)));
}

function computeOpenDeg(vw: number, vh: number, perspective: number): number {
  if (vw <= 720) return 80;
  const halfW = vw / 2;
  const targetHalfGap = Math.min(308, Math.max(246, vw * 0.225));
  for (let a = 32; a <= 78; a += 0.5) {
    const rad = (a * Math.PI) / 180;
    const denom = Math.max(240, perspective - halfW * Math.sin(rad));
    const projectedHalfGap = ((halfW - halfW * Math.cos(rad)) * perspective) / denom;
    if (projectedHalfGap >= targetHalfGap) return a;
  }
  return 74;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function AuthDoorLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeRealm: AuthRealm = location.pathname.startsWith("/admin") ? "admin" : "user";

  const [displayRealm, setDisplayRealm] = useState<AuthRealm>(routeRealm);
  const [isOpen, setIsOpen] = useState<boolean>(() => prefersReducedMotion());
  const [isLit, setIsLit] = useState<boolean>(() => prefersReducedMotion());
  const [isTransitioning, setIsTransitioning] = useState<boolean>(false);

  const doorsContainerRef = useRef<HTMLDivElement>(null);
  const leftDoorRef = useRef<HTMLDivElement>(null);
  const rightDoorRef = useRef<HTMLDivElement>(null);
  const leftShadeRef = useRef<HTMLDivElement>(null);
  const rightShadeRef = useRef<HTMLDivElement>(null);
  const dimRef = useRef<HTMLDivElement>(null);
  const chamberContentRef = useRef<HTMLDivElement>(null);

  const stateRef = useRef({
    angle: 0, // 0 = closed, negative = open outward toward viewer, positive = pressed in
    lit: 0,
    openDeg: 48,
    perspective: 2200,
    isOpen: false,
    token: 0,
    rafId: 0,
    entering: false,
  });

  const paint = useCallback((angle: number, lit: number) => {
    const s = stateRef.current;
    s.angle = angle;
    s.lit = lit;
    if (leftDoorRef.current) {
      leftDoorRef.current.style.transform = `rotateY(${angle.toFixed(3)}deg)`;
    }
    if (rightDoorRef.current) {
      rightDoorRef.current.style.transform = `rotateY(${(-angle).toFixed(3)}deg)`;
    }
    const shadeOpacity = (Math.max(0, -angle) / Math.max(30, s.openDeg)) * 0.42;
    if (leftShadeRef.current) leftShadeRef.current.style.opacity = shadeOpacity.toFixed(3);
    if (rightShadeRef.current) rightShadeRef.current.style.opacity = shadeOpacity.toFixed(3);
    if (dimRef.current) dimRef.current.style.opacity = (0.86 * (1 - lit)).toFixed(3);
    if (chamberContentRef.current) {
      const reveal = Math.min(1, Math.max(0, (lit - 0.12) / 0.88));
      chamberContentRef.current.style.opacity = reveal.toFixed(3);
      chamberContentRef.current.style.transform = `translateY(${((1 - reveal) * 14).toFixed(2)}px) scale(${(0.985 + reveal * 0.015).toFixed(4)})`;
    }
  }, []);

  const cancelAnimation = useCallback(() => {
    stateRef.current.token += 1;
    if (stateRef.current.rafId) {
      cancelAnimationFrame(stateRef.current.rafId);
      stateRef.current.rafId = 0;
    }
    return stateRef.current.token;
  }, []);

  const runTween = useCallback(
    (
      token: number,
      durationMs: number,
      onStep: (progress: number) => void,
    ): Promise<boolean> =>
      new Promise(resolve => {
        if (durationMs <= 0) {
          if (stateRef.current.token !== token) {
            resolve(false);
            return;
          }
          onStep(1);
          resolve(true);
          return;
        }
        const start = performance.now();
        const tick = (now: number) => {
          if (stateRef.current.token !== token) {
            resolve(false);
            return;
          }
          const t = Math.min(1, (now - start) / durationMs);
          onStep(t);
          if (t < 1) {
            stateRef.current.rafId = requestAnimationFrame(tick);
          } else {
            stateRef.current.rafId = 0;
            resolve(true);
          }
        };
        stateRef.current.rafId = requestAnimationFrame(tick);
      }),
    [],
  );

  const openDoors = useCallback(
    async (withDelayMs = 0) => {
      const s = stateRef.current;
      const token = cancelAnimation();
      if (prefersReducedMotion()) {
        s.isOpen = true;
        setIsOpen(true);
        setIsLit(true);
        setIsTransitioning(false);
        paint(-s.openDeg, 1);
        return;
      }
      setIsTransitioning(true);
      if (withDelayMs > 0) {
        const waited = await runTween(token, withDelayMs, () => {});
        if (!waited) return;
      }
      const startAngle = s.angle;
      const startLit = s.lit;
      // 1. Anticipation press-in (+1.8deg)
      if (startAngle > -4) {
        const ok1 = await runTween(token, 190, t => {
          const k = EASE.press(t);
          paint(startAngle + (1.8 - startAngle) * k, startLit);
        });
        if (!ok1) return;
      }
      // 2. Architectural 3D swing open + chamber illumination
      const fromAngle = s.angle;
      const targetAngle = -s.openDeg;
      setIsOpen(true);
      s.isOpen = true;
      let litFlagged = false;
      const ok2 = await runTween(token, 520, t => {
        const angleK = EASE.open(t);
        const litK = EASE.fade(Math.min(1, t / 0.78));
        if (!litFlagged && t >= 0.18) {
          litFlagged = true;
          setIsLit(true);
        }
        paint(fromAngle + (targetAngle - fromAngle) * angleK, startLit + (1 - startLit) * litK);
      });
      if (!ok2) return;
      setIsLit(true);
      setIsTransitioning(false);
    },
    [cancelAnimation, paint, runTween],
  );

  const closeDoors = useCallback(
    async (): Promise<boolean> => {
      const s = stateRef.current;
      const token = cancelAnimation();
      if (prefersReducedMotion()) {
        s.isOpen = false;
        setIsOpen(false);
        setIsLit(false);
        setIsTransitioning(false);
        paint(0, 0);
        return true;
      }
      setIsTransitioning(true);
      const fromAngle = s.angle;
      const fromLit = s.lit;
      // 1. Weighted slam shut with +1.5deg overshoot
      const ok1 = await runTween(token, 360, t => {
        const angleK = EASE.closeIn(t);
        const litK = EASE.fade(Math.min(1, t / 0.75));
        paint(fromAngle + (1.5 - fromAngle) * angleK, fromLit * (1 - litK));
      });
      if (!ok1) return false;
      s.isOpen = false;
      setIsOpen(false);
      setIsLit(false);
      // 2. Settle spring back to 0deg
      const ok2 = await runTween(token, 160, t => {
        const k = EASE.spring(t);
        paint(1.5 * (1 - k), 0);
      });
      if (!ok2) return false;
      setIsTransitioning(false);
      return true;
    },
    [cancelAnimation, paint, runTween],
  );

  const toggleDoors = useCallback(() => {
    if (stateRef.current.entering) return;
    if (stateRef.current.isOpen) {
      void closeDoors();
    } else {
      void openDoors(0);
    }
  }, [closeDoors, openDoors]);

  const switchRealm = useCallback(
    (target: AuthRealm) => {
      if (stateRef.current.entering) return;
      const targetPath = target === "admin" ? "/admin/login" : "/login";
      if (target === displayRealm && location.pathname === targetPath) {
        if (!stateRef.current.isOpen) void openDoors(0);
        return;
      }
      if (prefersReducedMotion()) {
        setDisplayRealm(target);
        navigate(targetPath);
        return;
      }
      void (async () => {
        // Close doors -> swap chamber behind closed doors -> swing open into the new world
        const closed = await closeDoors();
        if (!closed) return;
        setDisplayRealm(target);
        navigate(targetPath);
        await openDoors(90);
      })();
    },
    [closeDoors, displayRealm, location.pathname, navigate, openDoors],
  );

  const enterPortal = useCallback(
    (to: string, options?: NavigateOptions) => {
      if (prefersReducedMotion()) {
        navigate(to, options);
        return;
      }
      const s = stateRef.current;
      s.entering = true;
      const token = cancelAnimation();
      const fromAngle = s.angle;
      const wideAngle = -Math.max(s.openDeg + 26, 76);
      setIsTransitioning(true);
      void runTween(token, 360, t => {
        const k = EASE.open(t);
        paint(fromAngle + (wideAngle - fromAngle) * k, 1);
      }).then(() => {
        navigate(to, options);
      });
    },
    [cancelAnimation, navigate, paint, runTween],
  );

  // Sync displayRealm when user navigates directly (e.g. browser back/forward or link clicks)
  useEffect(() => {
    if (routeRealm !== displayRealm && !isTransitioning) {
      setDisplayRealm(routeRealm);
    }
  }, [routeRealm, displayRealm, isTransitioning]);

  // Measure viewport perspective & open angle + auto-open on initial mount (Scheme A)
  useLayoutEffect(() => {
    const syncGeometry = () => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const perspective = computePerspective(vh);
      const openDeg = computeOpenDeg(vw, vh, perspective);
      stateRef.current.perspective = perspective;
      stateRef.current.openDeg = openDeg;
      if (doorsContainerRef.current) {
        doorsContainerRef.current.style.perspective = `${perspective}px`;
      }
      if (stateRef.current.isOpen && !stateRef.current.rafId) {
        paint(-openDeg, stateRef.current.lit);
      }
    };
    syncGeometry();
    window.addEventListener("resize", syncGeometry);
    return () => window.removeEventListener("resize", syncGeometry);
  }, [paint]);

  useEffect(() => {
    paint(0, 0);
    void openDoors(220);
    return () => {
      cancelAnimation();
    };
    // Run initial entrance reveal once on layout mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isAdmin = displayRealm === "admin";

  return (
    <AuthDoorContext.Provider
      value={{
        realm: displayRealm,
        isOpen,
        isTransitioning,
        toggleDoors,
        switchRealm,
        enterPortal,
      }}
    >
      <main
        className={`auth-vault-stage ${isAdmin ? "is-admin" : "is-user"} ${isLit ? "is-lit" : ""} ${isOpen ? "is-open" : "is-closed"}`}
        data-realm={displayRealm}
      >
        {/* Shared SVG Blind-Deboss Filter (deep maritime slate inner shadow + rim highlight) */}
        <svg className="auth-svg-defs" width="0" height="0" aria-hidden="true">
          <defs>
            <filter id="zhouxu-deboss" x="-6%" y="-10%" width="112%" height="126%" colorInterpolationFilters="sRGB">
              <feGaussianBlur in="SourceAlpha" stdDeviation="2.6" result="b" />
              <feOffset in="b" dx="3" dy="3.6" result="ob" />
              <feComposite in="SourceAlpha" in2="ob" operator="arithmetic" k2="1" k3="-1" result="innerShadowMask" />
              <feFlood floodColor="#020407" floodOpacity="0.88" result="shadowColor" />
              <feComposite in="shadowColor" in2="innerShadowMask" operator="in" result="innerShadow" />

              <feOffset in="b" dx="-1.8" dy="-2.2" result="ob2" />
              <feComposite in="SourceAlpha" in2="ob2" operator="arithmetic" k2="1" k3="-1" result="innerHiMask" />
              <feFlood floodColor={isAdmin ? "#e8cfa0" : "#b8d6f0"} floodOpacity="0.22" result="hiColor" />
              <feComposite in="hiColor" in2="innerHiMask" operator="in" result="innerHi" />

              <feGaussianBlur in="SourceAlpha" stdDeviation="1" result="rb" />
              <feOffset in="rb" dx="1.2" dy="1.6" result="orb" />
              <feComposite in="orb" in2="SourceAlpha" operator="out" result="rimMask" />
              <feFlood floodColor={isAdmin ? "#e6c993" : "#9dc3e2"} floodOpacity="0.16" result="rimColor" />
              <feComposite in="rimColor" in2="rimMask" operator="in" result="rimLight" />

              <feMerge>
                <feMergeNode in="SourceGraphic" />
                <feMergeNode in="innerShadow" />
                <feMergeNode in="innerHi" />
                <feMergeNode in="rimLight" />
              </feMerge>
            </filter>
          </defs>
        </svg>

        {/* Layer 1: The Chamber Interior behind the doors (unified HexagonalVault, color-shifted by realm) */}
        <div className="auth-chamber" aria-hidden={!isOpen}>
          <div className="auth-chamber-backdrop" aria-hidden="true">
            <AuthVaultBackground realm={displayRealm} />
            <div className="auth-chamber-skylight" />
            <div className="auth-chamber-vignette" />
          </div>
          <div className="auth-chamber-dim" ref={dimRef} aria-hidden="true" />

          <div
            className="auth-chamber-content"
            ref={chamberContentRef}
            style={{ pointerEvents: isOpen ? "auto" : "none" }}
          >
            <Outlet />
          </div>
        </div>

        {/* Layer 2: Real CSS 3D Hinged Archival Doors */}
        <div className="auth-doors" id="auth-doors" ref={doorsContainerRef}>
          {/* Left Door */}
          <div className="auth-door auth-door-left" ref={leftDoorRef}>
            <div className="auth-door-face">
              <div className="auth-door-grain" aria-hidden="true" />
              <div className="auth-door-engrave auth-door-engrave-left" aria-hidden="true">
                <span className="auth-engrave-index">FOLIO · 01</span>
                <span className="auth-engrave-meta">ZHOUXU ARCHIVE</span>
              </div>

              <svg
                className="auth-door-emboss auth-door-emboss-left"
                viewBox="0 0 460 190"
                preserveAspectRatio="xMaxYMax meet"
                aria-hidden="true"
              >
                <g filter="url(#zhouxu-deboss)" fill={isAdmin ? "#131416" : "#111a24"}>
                  <text
                    x="456"
                    y="164"
                    textAnchor="end"
                    fontFamily="'Instrument Serif', Georgia, serif"
                    fontStyle="italic"
                    fontSize="168"
                    letterSpacing="-4"
                  >
                    Zhou
                  </text>
                </g>
              </svg>

              <div className="auth-door-shade" ref={leftShadeRef} aria-hidden="true" />
              <div className="auth-door-seam" aria-hidden="true" />

              <button
                type="button"
                className="auth-door-handle auth-door-handle-left"
                aria-controls="auth-doors"
                aria-expanded={isOpen}
                aria-label={isOpen ? "合上舟序大门" : "推开舟序大门"}
                title={isOpen ? "点击合上大门" : "点击推开大门"}
                onClick={toggleDoors}
              >
                <span className="auth-handle-slot" aria-hidden="true" />
                <span className="auth-handle-pip" aria-hidden="true" />
              </button>
            </div>
            <div className="auth-door-edge" aria-hidden="true" />
          </div>

          {/* Right Door */}
          <div className="auth-door auth-door-right" ref={rightDoorRef}>
            <div className="auth-door-face">
              <div className="auth-door-grain" aria-hidden="true" />
              <div className="auth-door-engrave auth-door-engrave-right" aria-hidden="true">
                <span className="auth-engrave-index">{isAdmin ? "VAULT · II" : "VAULT · I"}</span>
                <span className="auth-engrave-meta">{isAdmin ? "GOVERNANCE" : "WORKSPACE"}</span>
              </div>

              <svg
                className="auth-door-emboss auth-door-emboss-right"
                viewBox="0 0 460 190"
                preserveAspectRatio="xMinYMax meet"
                aria-hidden="true"
              >
                <g filter="url(#zhouxu-deboss)" fill={isAdmin ? "#131416" : "#111a24"}>
                  <text
                    x="4"
                    y="164"
                    textAnchor="start"
                    fontFamily="'Instrument Serif', Georgia, serif"
                    fontStyle="italic"
                    fontSize="168"
                    letterSpacing="-4"
                  >
                    xu.
                  </text>
                </g>
              </svg>

              <div className="auth-door-shade" ref={rightShadeRef} aria-hidden="true" />
              <div className="auth-door-seam" aria-hidden="true" />

              <button
                type="button"
                className="auth-door-handle auth-door-handle-right"
                aria-controls="auth-doors"
                aria-expanded={isOpen}
                aria-label={isOpen ? "合上舟序大门" : "推开舟序大门"}
                title={isOpen ? "点击合上大门" : "点击推开大门"}
                onClick={toggleDoors}
              >
                <span className="auth-handle-slot" aria-hidden="true" />
                <span className="auth-handle-pip" aria-hidden="true" />
              </button>
            </div>
            <div className="auth-door-edge" aria-hidden="true" />
          </div>
        </div>

        {/* Layer 3: Symmetrical 3-Part Floating Navigation (Center-Axis Aligned!) */}
        <header className="auth-topbar">
          <div className="auth-topbar-left">
            <Link className="auth-back-link" to="/">
              <ArrowLeft size={14} aria-hidden="true" />
              <span>返回首页</span>
            </Link>
          </div>

          <Link className="auth-brand-center" to="/" aria-label="舟序，返回首页">
            <img src="/zhou-xu-mark.png" alt="" width="24" height="24" />
            <span className="auth-wordmark" lang="en">Zhouxu<span aria-hidden="true">.</span></span>
            <span className="auth-brand-divider" aria-hidden="true" />
            <span className="auth-brand-cn">舟序</span>
          </Link>

          <div className="auth-topbar-right">
            <div className="auth-realm-switch" role="tablist" aria-label="入口通道选择">
              <button
                type="button"
                role="tab"
                aria-selected={!isAdmin}
                className={`auth-realm-tab ${!isAdmin ? "is-active" : ""}`}
                onClick={() => switchRealm("user")}
              >
                <span className="auth-realm-dot" aria-hidden="true" />
                用户
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={isAdmin}
                className={`auth-realm-tab ${isAdmin ? "is-active" : ""}`}
                onClick={() => switchRealm("admin")}
              >
                <ShieldCheck size={12} aria-hidden="true" />
                管理员
              </button>
            </div>
          </div>
        </header>

        {/* Quiet bottom door cue when closed */}
        {!isOpen && (
          <div className="auth-closed-cue">
            <button type="button" className="auth-closed-cue-btn" onClick={toggleDoors}>
              轻触拉手推开{isAdmin ? "管理台" : "舟序"}大门
            </button>
          </div>
        )}
      </main>
    </AuthDoorContext.Provider>
  );
}
