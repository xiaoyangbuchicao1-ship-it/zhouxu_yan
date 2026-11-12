import { useSyncExternalStore } from "react";
import { isAuthSession, type AuthSession } from "./auth";

const STORAGE_KEY = "zhouxu.auth.session.v1";

function readSession(): AuthSession | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    return isAuthSession(value) ? value : null;
  } catch { return null; }
}

let currentSession = readSession();
let signedOutNotice: string | null = null;
const listeners = new Set<() => void>();

export function getSignedOutNotice() { return signedOutNotice; }

export function saveSession(session: AuthSession | null, notice?: string) {
  try {
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage can be disabled by the browser. Keep the current in-memory session
    // usable, without persisting either a password or anything to localStorage.
  }
  currentSession = session;
  signedOutNotice = session ? null : notice || null;
  listeners.forEach(listener => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useAuthSession() {
  return useSyncExternalStore(subscribe, () => currentSession, () => null);
}
