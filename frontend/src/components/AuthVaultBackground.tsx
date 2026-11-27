import { Component, Suspense, lazy, type ReactNode } from "react";

// Keep Three.js out of the landing page's initial bundle.
const HexagonalVault = lazy(() => import("./react-bits/hexagonal-vault"));
const USER_COLORS = ["#527da9", "#b2cddd"];
const ADMIN_COLORS = ["#9f7f4c", "#e6cba0"];
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

export default function AuthVaultBackground({ realm = "user" }: { realm?: "user" | "admin" }) {
  const isAdmin = realm === "admin";
  return (
    <BackgroundBoundary>
      <Suspense fallback={fallback}>
        <HexagonalVault
          className="auth-vault"
          colors={isAdmin ? ADMIN_COLORS : USER_COLORS}
          backgroundColor={isAdmin ? "#080706" : "#060b11"}
          cellSize={1.6}
          lattice={0.55}
          glow={1.2}
          grain={0.25}
          speed={0.6}
          dpr={1.25}
          clickPulses={false}
          eventSource="window"
        />
      </Suspense>
    </BackgroundBoundary>
  );
}
