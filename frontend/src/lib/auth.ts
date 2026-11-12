export type AuthMode = "login" | "register";

export interface AuthCredentials {
  email: string;
  password: string;
  display_name?: string;
}

export interface AuthSession {
  account: { id: string; email: string; display_name: string };
  session: { token: string };
}

export class AuthApiError extends Error {
  constructor(public code: string, public status = 0) {
    super(authErrorMessage(code));
    this.name = "AuthApiError";
  }
}

export function authErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    email_invalid: "请填写有效的邮箱地址。",
    password_length_invalid: "密码需要至少 12 个字符。",
    account_email_exists: "这个邮箱已经注册，可以直接登录。",
    authentication_failed: "邮箱或密码不正确，请核对后重试。",
    authentication_required: "登录状态已失效，请重新登录。",
    invalid_response: "服务返回的信息不完整，请稍后重试。",
    timeout: "连接时间有些久，请稍后重试。",
    network: "暂时无法连接舟序服务，请检查网络或稍后重试。",
    unavailable: "舟序服务暂时不可用，请稍后重试。",
  };
  return messages[code] ?? "这次操作未能完成，请核对信息后重试。";
}

export function validateCredentials(mode: AuthMode, values: AuthCredentials) {
  const errors: Partial<Record<keyof AuthCredentials, string>> = {};
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim())) {
    errors.email = "请填写有效的邮箱地址。";
  }
  if (!values.password) {
    errors.password = "请输入密码。";
  } else if (mode === "register" && [...values.password].length < 12) {
    errors.password = "密码需要至少 12 个字符。";
  }
  return errors;
}

export function isAuthSession(value: unknown): value is AuthSession {
  if (!value || typeof value !== "object") return false;
  const candidate = value as AuthSession;
  return typeof candidate.account?.id === "string" && Boolean(candidate.account.id)
    && typeof candidate.account?.email === "string"
    && typeof candidate.account?.display_name === "string"
    && typeof candidate.session?.token === "string" && Boolean(candidate.session.token);
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

async function request(path: string, options: RequestInit = {}, signal?: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
  try {
    const response = await fetch(`${apiBase}${path}`, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const code = typeof data?.detail === "string" ? data.detail
        : response.status >= 500 ? "unavailable" : "request_failed";
      throw new AuthApiError(code, response.status);
    }
    if (data === null) throw new AuthApiError("invalid_response");
    return data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof AuthApiError) throw error;
    throw new AuthApiError(timedOut ? "timeout" : "network");
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export async function authenticate(mode: AuthMode, values: AuthCredentials, signal?: AbortSignal) {
  const payload: AuthCredentials = { email: values.email.trim().toLowerCase(), password: values.password };
  if (mode === "register") payload.display_name = values.display_name?.trim() || "";
  const data = await request(`/auth/${mode}`, { method: "POST", body: JSON.stringify(payload) }, signal);
  if (!isAuthSession(data)) throw new AuthApiError("invalid_response");
  return data;
}

// This read-only route verifies the bearer session without loading workspace data.
export async function verifySession(token: string, signal?: AbortSignal) {
  const data = await request("/auth/me", { headers: { Authorization: `Bearer ${token}` } }, signal);
  if (!data || typeof data !== "object" || typeof (data as { id?: unknown }).id !== "string") {
    throw new AuthApiError("invalid_response");
  }
  return data;
}

export async function revokeSession(token: string, signal?: AbortSignal) {
  await request("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }, signal);
}
