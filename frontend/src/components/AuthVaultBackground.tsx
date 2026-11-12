import { Component, Suspense, lazy, type ReactNode } from "react";

// Keep Three.js out of the landing page's initial bundle.
const HexagonalVault = lazy(() => import("./react-bits/hexagonal-vault"));
const colors = ["#527da9", "#b2cddd"];
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

export default function AuthVaultBackground() {
  return (
    <BackgroundBoundary>
      <Suspense fallback={fallback}>
        <HexagonalVault
          className="auth-vault"
          colors={colors}
          backgroundColor="#080c10"
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
