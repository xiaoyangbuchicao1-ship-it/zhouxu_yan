import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { Link, Outlet, useLocation, useNavigate, type NavigateOptions } from "react-router-dom";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import "@/pages/auth.css";

export type AuthRealm = "user" | "admin";

interface AuthDoorContextValue {
  realm: AuthRealm;
  isTransitioning: boolean;
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

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function AuthDoorLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeRealm: AuthRealm = location.pathname.startsWith("/admin") ? "admin" : "user";

  const [displayRealm, setDisplayRealm] = useState<AuthRealm>(routeRealm);
  const [rippleKey, setRippleKey] = useState<number>(1);
  const [rippleBoost, setRippleBoost] = useState<number>(0.68);
  const [isPulsingCard, setIsPulsingCard] = useState<boolean>(false);
  const [isEntering, setIsEntering] = useState<boolean>(false);

  const stageRef = useRef<HTMLElement>(null);
  const timersRef = useRef<number[]>([]);
  const prevPathRef = useRef<string>(location.pathname);

  const clearTimers = useCallback(() => {
    for (const id of timersRef.current) {
      window.clearTimeout(id);
    }
    timersRef.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const triggerCardPulse = useCallback(
    (boost: number) => {
      setRippleBoost(boost);
      setRippleKey(k => k + 1);
      if (prefersReducedMotion()) return;
      setIsPulsingCard(true);
      const t = window.setTimeout(() => {
        setIsPulsingCard(false);
      }, 360);
      timersRef.current.push(t);
    },
    [],
  );

  // Sync realm & emit gentle center ripple when navigating between /login <-> /register or browser history
  useEffect(() => {
    if (prevPathRef.current !== location.pathname) {
      const prevRealm: AuthRealm = prevPathRef.current.startsWith("/admin") ? "admin" : "user";
      prevPathRef.current = location.pathname;
      if (routeRealm !== displayRealm) {
        setDisplayRealm(routeRealm);
        triggerCardPulse(0.75);
      } else if (prevRealm === routeRealm) {
        // Switching between /login and /register within user realm
        triggerCardPulse(0.54);
      }
    }
  }, [location.pathname, routeRealm, displayRealm, triggerCardPulse]);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const el = stageRef.current;
    if (!el || event.pointerType === "touch") return;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    el.style.setProperty("--mx", `${x.toFixed(2)}%`);
    el.style.setProperty("--my", `${y.toFixed(2)}%`);
  }, []);

  const switchRealm = useCallback(
    (target: AuthRealm) => {
      if (isEntering) return;
      const targetPath = target === "admin" ? "/admin/login" : "/login";
      if (target === displayRealm && location.pathname === targetPath) return;

      prevPathRef.current = targetPath;
      setDisplayRealm(target);
      triggerCardPulse(0.75);
      navigate(targetPath);
    },
    [displayRealm, isEntering, location.pathname, navigate, triggerCardPulse],
  );

  const enterPortal = useCallback(
    (to: string, options?: NavigateOptions) => {
      if (prefersReducedMotion()) {
        navigate(to, options);
        return;
      }
      clearTimers();
      setRippleBoost(0.88);
      setRippleKey(k => k + 1);
      setIsEntering(true);

      const navTimer = window.setTimeout(() => {
        navigate(to, options);
      }, 340);
      timersRef.current.push(navTimer);
    },
    [clearTimers, navigate],
  );

  const isAdmin = displayRealm === "admin";
  const isTransitioning = isPulsingCard || isEntering;

  return (
    <AuthDoorContext.Provider
      value={{
        realm: displayRealm,
        isTransitioning,
        switchRealm,
        enterPortal,
      }}
    >
      <main
        ref={stageRef}
        onPointerMove={handlePointerMove}
        className={[
          "auth-vault-stage",
          isAdmin ? "is-admin" : "is-user",
          isPulsingCard ? "is-rippling" : "",
          isEntering ? "is-entering" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        data-realm={displayRealm}
      >
        {/* Layer 1: Panoramic Hexagonal Vault with Real Center-Outward Ripple Shader */}
        <div className="auth-stage-backdrop" aria-hidden="true">
          <AuthVaultBackground
            realm={displayRealm}
            rippleKey={rippleKey}
            rippleBoost={rippleBoost}
          />
          <div className="auth-stage-skylight" />
          <div className="auth-stage-cursor-glow" />
          <div className="auth-stage-vignette" />
        </div>

        {/* Layer 2: Symmetrical 3-Part Floating Navigation */}
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

        {/* Layer 3: Open, Unobstructed Centered Auth Surface (Origin of Ripples) */}
        <div className="auth-chamber">
          <div key={location.pathname} className="auth-chamber-content">
            <Outlet />
          </div>
        </div>

        {/* Quiet Spatial Baseline Mark */}
        <footer className="auth-stage-foot" aria-hidden="true">
          <span className="auth-stage-foot-line" />
          <span className="auth-stage-foot-label">
            {isAdmin ? "ZHOUXU · GOVERNANCE VAULT" : "ZHOUXU · MARITIME RESEARCH"}
          </span>
          <span className="auth-stage-foot-line" />
        </footer>
      </main>
    </AuthDoorContext.Provider>
  );
}
