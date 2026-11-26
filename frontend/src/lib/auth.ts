export type AuthMode = "login" | "register";

export interface AuthCredentials {
  email: string;
  password: string;
  display_name?: string;
  verification_code?: string;
}

export type VerificationPurpose = "register" | "reset_password";

export interface AuthConfig {
  email_verification_enabled: boolean;
  allow_password_reset: boolean;
  captcha_enabled?: boolean;
}

export interface CaptchaChallenge {
  challenge_id: string;
  bg_image: string;
  piece_image: string;
  y: number;
  width: number;
  height: number;
  piece_width: number;
  piece_height: number;
}

export interface CaptchaVerifyResult {
  verified: boolean;
  captcha_token?: string | null;
  error?: string | null;
}

export interface SendCodeResult {
  sent: boolean;
  expires_in_seconds: number;
  cooldown_seconds: number;
}

export interface ResetPasswordPayload {
  email: string;
  verification_code: string;
  new_password: string;
}

export interface AuthSession {
  account: { id: string; email: string; display_name: string; role: "user" | "admin" };
  session: { token: string };
}

export type AccountRole = AuthSession["account"]["role"];
export type AccountView = AuthSession["account"];
export type AuthAccount = AccountView;

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
    email_not_registered: "这个邮箱还没有注册账户。",
    admin_required: "这个入口只对管理员开放。",
    authentication_failed: "邮箱或密码不正确，请核对后重试。",
    account_disabled: "这个账户已被停用，请联系管理员。",
    authentication_required: "登录状态已失效，请重新登录。",
    verification_purpose_invalid: "验证码用途无效，请刷新页面后重试。",
    verification_cooldown: "发送太频繁，请等待 60 秒后再试。",
    verification_rate_limited: "请求验证码过于频繁，请稍后再试。",
    auth_rate_limited: "尝试次数过多，请 10 分钟后再试。",
    verification_code_invalid: "验证码不正确，请核对后重试。",
    verification_code_expired: "验证码已过期，请重新发送。",
    verification_code_exhausted: "验证码错误次数过多，请重新获取。",
    captcha_required: "请先完成滑块安全验证。",
    captcha_invalid: "安全验证未通过，请重新滑动。",
    email_provider_unconfigured: "系统发信服务尚未配置，请联系管理员。",
    email_send_failed: "验证邮件发送失败，请稍后重试或联系管理员。",
    password_reset_disabled: "管理员暂未开放自助找回密码功能。",
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
    && (candidate.account?.role === "user" || candidate.account?.role === "admin")
    && typeof candidate.session?.token === "string" && Boolean(candidate.session.token);
}

const runtimeEnv: Record<string, string | undefined> =
  (import.meta as ImportMeta & { env?: Record<string, string> }).env ?? {};
const apiBase = (runtimeEnv.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
export function resolveAuthApiBase() { return apiBase; }

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

export async function fetchAuthConfig(signal?: AbortSignal): Promise<AuthConfig> {
  const data = await request("/auth/config", {}, signal);
  if (!data || typeof data !== "object") throw new AuthApiError("invalid_response");
  const candidate = data as Record<string, unknown>;
  return {
    email_verification_enabled: Boolean(candidate.email_verification_enabled),
    allow_password_reset: Boolean(candidate.allow_password_reset),
    captcha_enabled: candidate.captcha_enabled !== undefined ? Boolean(candidate.captcha_enabled) : true,
  };
}

export async function requestCaptchaChallenge(signal?: AbortSignal): Promise<CaptchaChallenge> {
  const data = await request("/auth/captcha/challenge", { method: "POST" }, signal);
  if (!data || typeof data !== "object") throw new AuthApiError("invalid_response");
  return data as CaptchaChallenge;
}

export async function verifyCaptcha(
  payload: { challenge_id: string; x: number; duration_ms: number; trail?: number[][] },
  signal?: AbortSignal,
): Promise<CaptchaVerifyResult> {
  const data = await request(
    "/auth/captcha/verify",
    { method: "POST", body: JSON.stringify(payload) },
    signal,
  );
  if (!data || typeof data !== "object") throw new AuthApiError("invalid_response");
  return data as CaptchaVerifyResult;
}

export async function sendVerificationCode(
  email: string,
  purpose: VerificationPurpose,
  captcha_token?: string,
  signal?: AbortSignal,
): Promise<SendCodeResult> {
  const data = await request(
    "/auth/send-code",
    {
      method: "POST",
      body: JSON.stringify({ email: email.trim().toLowerCase(), purpose, captcha_token }),
    },
    signal,
  );
  if (!data || typeof data !== "object") throw new AuthApiError("invalid_response");
  const candidate = data as Record<string, unknown>;
  return {
    sent: Boolean(candidate.sent),
    expires_in_seconds: Number(candidate.expires_in_seconds ?? 300),
    cooldown_seconds: Number(candidate.cooldown_seconds ?? 60),
  };
}

export async function resetPassword(values: ResetPasswordPayload, signal?: AbortSignal) {
  const payload = {
    email: values.email.trim().toLowerCase(),
    verification_code: values.verification_code.trim(),
    new_password: values.new_password,
  };
  const data = await request("/auth/reset-password", { method: "POST", body: JSON.stringify(payload) }, signal);
  if (!isAuthSession(data)) throw new AuthApiError("invalid_response");
  return data;
}

export async function authenticate(
  mode: AuthMode,
  values: AuthCredentials,
  signal?: AbortSignal,
  captcha_token?: string,
) {
  const payload: AuthCredentials & { captcha_token?: string } = {
    email: values.email.trim().toLowerCase(),
    password: values.password,
  };
  if (mode === "register") {
    payload.display_name = values.display_name?.trim() || "";
    if (values.verification_code?.trim()) {
      payload.verification_code = values.verification_code.trim();
    }
    if (captcha_token) {
      payload.captcha_token = captcha_token;
    }
  }
  const data = await request(`/auth/${mode}`, { method: "POST", body: JSON.stringify(payload) }, signal);
  if (!isAuthSession(data)) throw new AuthApiError("invalid_response");
  return data;
}

export async function authenticateAdmin(values: AuthCredentials, signal?: AbortSignal) {
  const payload: AuthCredentials = { email: values.email.trim().toLowerCase(), password: values.password };
  const data = await request("/auth/admin-login", { method: "POST", body: JSON.stringify(payload) }, signal);
  if (!isAuthSession(data) || data.account.role !== "admin") throw new AuthApiError("invalid_response");
  return data;
}

// This read-only route verifies the bearer session without loading workspace data.
export async function verifySession(token: string, signal?: AbortSignal) {
  const data = await request("/auth/me", { headers: { Authorization: `Bearer ${token}` } }, signal);
  if (!data || typeof data !== "object" || typeof (data as { id?: unknown }).id !== "string"
    || ((data as { role?: unknown }).role !== "user" && (data as { role?: unknown }).role !== "admin")) {
    throw new AuthApiError("invalid_response");
  }
  return data;
}

export async function revokeSession(token: string, signal?: AbortSignal) {
  await request("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }, signal);
}
