import { Component, Suspense, lazy, type ReactNode } from "react";

// Keep Three.js out of the landing page's initial bundle.
const HexagonalVault = lazy(() => import("./react-bits/hexagonal-vault"));
const USER_COLORS = ["#527da9", "#b8d7eb"];
const ADMIN_COLORS = ["#a6834b", "#ebd1a4"];
const fallback = <div className="auth-vault-fallback" />;

class BackgroundBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? fallback : this.props.children;
  }
}

export interface AuthVaultBackgroundProps {
  realm?: "user" | "admin";
  rippleKey?: number;
  rippleBoost?: number;
}

export default function AuthVaultBackground({
  realm = "user",
  rippleKey = 0,
  rippleBoost = 1,
}: AuthVaultBackgroundProps) {
  const isAdmin = realm === "admin";
  return (
    <BackgroundBoundary>
      <Suspense fallback={fallback}>
        <HexagonalVault
          className="auth-vault"
          colors={isAdmin ? ADMIN_COLORS : USER_COLORS}
          backgroundColor={isAdmin ? "#080706" : "#060b11"}
          cellSize={1.58}
          lattice={0.56}
          glow={1.32}
          core={0.52}
          grain={0.24}
          speed={0.62}
          dpr={1.35}
          clickPulses={true}
          pulseSpeed={1.15}
          centerRippleKey={rippleKey}
          centerRippleStrength={rippleBoost}
          eventSource="window"
        />
      </Suspense>
    </BackgroundBoundary>
  );
}
