import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, LoaderCircle, LogOut } from "lucide-react";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import CaptchaSliderModal from "@/components/CaptchaSliderModal";
import {
  authenticate,
  AuthApiError,
  fetchAuthConfig,
  resetPassword,
  revokeSession,
  sendVerificationCode,
  validateCredentials,
  verifySession,
  type AuthConfig,
  type AuthCredentials,
  type AuthMode,
} from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
import { createAccountSettingsApi } from "@/lib/account-settings-api";
import "./auth.css";

export default function AuthPage({ mode = "login" }: { mode?: AuthMode | "account" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useAuthSession();
  const register = mode === "register";
  const [email, setEmail] = useState(location.state?.email || "");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [authConfig, setAuthConfig] = useState<AuthConfig>({
    email_verification_enabled: false,
    allow_password_reset: false,
    captcha_enabled: true,
  });
  const [captchaOpen, setCaptchaOpen] = useState(false);
  const [pendingCaptchaAction, setPendingCaptchaAction] = useState<"send_code" | "register">("send_code");
  const [resetMode, setResetMode] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [codeCooldown, setCodeCooldown] = useState(0);
  const [codeNotice, setCodeNotice] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(Boolean(session));
  const [verified, setVerified] = useState(false);
  const [retry, setRetry] = useState(0);
  const [accountName, setAccountName] = useState(session?.account.display_name || "");
  const [accountSaved, setAccountSaved] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof AuthCredentials, string>>>({});
  const requestRef = useRef<AbortController | null>(null);
  const redirectingRef = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const token = session?.session.token;

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    const previousTitle = document.title;
    document.title = `${mode === "account" ? "我的账户" : resetMode ? "重置密码" : register ? "注册" : "登录"} · 舟序 Zhouxu`;
    setPassword("");
    setVerificationCode("");
    setCodeNotice("");
    setReveal(false);
    setFieldErrors({});
    setError(null);
    setErrorCode("");
    setBusy(false);
    redirectingRef.current = false;
    return () => { requestRef.current?.abort(); document.title = previousTitle; };
  }, [mode, register]);

  useEffect(() => {
    setResetMode(false);
    setCodeCooldown(0);
  }, [mode]);

  useEffect(() => {
    if (mode === "account") return;
    const controller = new AbortController();
    fetchAuthConfig(controller.signal)
      .then(cfg => {
        if (!controller.signal.aborted) setAuthConfig(cfg);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [mode]);

  useEffect(() => {
    if (codeCooldown <= 0) return;
    const timer = window.setInterval(() => {
      setCodeCooldown(prev => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [codeCooldown]);

  useEffect(() => {
    if (!token) {
      setVerified(false);
      setChecking(false);
      if (mode === "account" && !redirectingRef.current) navigate("/login", { replace: true });
      return;
    }
    const controller = new AbortController();
    setChecking(true);
    setVerified(false);
    verifySession(token, controller.signal).then(() => {
      if (controller.signal.aborted) return;
      setVerified(true);
      setError(null);
      if (mode !== "account") navigate("/workspace", { replace: true });
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (error instanceof AuthApiError && error.status === 401) {
        redirectingRef.current = true;
        navigate("/login", { replace: true, state: { notice: "登录状态已失效，请重新登录。" } });
        saveSession(null);
      } else {
        setError(error instanceof Error ? error.message : "暂时无法确认登录状态，请稍后重试。");
      }
    }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [token, mode, navigate, retry]);

  useEffect(() => {
    if (session?.account.display_name) setAccountName(session.account.display_name);
  }, [session?.account.display_name]);

  useEffect(() => { if (error) alertRef.current?.focus(); }, [error]);

  async function saveAccountDisplayName(event: FormEvent) {
    event.preventDefault();
    if (!session || busy) return;
    const trimmed = accountName.trim();
    if (!trimmed) {
      setError("请输入称呼。");
      return;
    }
    setBusy(true);
    setError(null);
    setAccountSaved("");
    try {
      const updated = await createAccountSettingsApi(session.session.token).updateProfile(trimmed);
      saveSession({ ...session, account: updated });
      setAccountSaved("称呼已更新。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存未完成，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  function handleSendCode() {
    if (sendingCode || busy || codeCooldown > 0 || mode === "account") return;
    setError(null);
    setErrorCode("");
    setCodeNotice("");
    const normalizedEmail = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setFieldErrors(errors => ({ ...errors, email: "请填写有效的邮箱地址。" }));
      formRef.current?.querySelector<HTMLInputElement>('[name="email"]')?.focus();
      return;
    }
    if (authConfig.captcha_enabled !== false) {
      setPendingCaptchaAction("send_code");
      setCaptchaOpen(true);
    } else {
      void executeSendCode();
    }
  }

  async function executeSendCode(captchaToken?: string) {
    const normalizedEmail = email.trim();
    setSendingCode(true);
    try {
      const purpose = resetMode ? "reset_password" : "register";
      const res = await sendVerificationCode(normalizedEmail, purpose, captchaToken);
      setCodeCooldown(res.cooldown_seconds || 60);
      setCodeNotice("验证码已发送至邮箱，5 分钟内有效。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败，请稍后重试。");
      setErrorCode(err instanceof AuthApiError ? err.code : "");
    } finally {
      setSendingCode(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || mode === "account") return;
    setError(null);
    setErrorCode("");
    const effectiveMode: AuthMode = resetMode ? "register" : mode === "login" ? "login" : "register";
    const values: AuthCredentials = {
      email,
      password,
      display_name: name,
      verification_code: verificationCode.trim() || undefined,
    };
    const errors = validateCredentials(effectiveMode, values);
    const needCode = resetMode || (register && authConfig.email_verification_enabled);
    if (needCode && !/^\d{6}$/.test(verificationCode.trim())) {
      errors.verification_code = "请输入 6 位数字验证码。";
    }
    setFieldErrors(errors);
    const firstError = Object.keys(errors)[0];
    if (firstError) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${firstError}"]`)?.focus();
      return;
    }

    if (effectiveMode === "register" && !needCode && authConfig.captcha_enabled !== false) {
      setPendingCaptchaAction("register");
      setCaptchaOpen(true);
      return;
    }

    void executeSubmit();
  }

  async function executeSubmit(captchaToken?: string) {
    const effectiveMode: AuthMode = resetMode ? "register" : mode === "login" ? "login" : "register";
    const values: AuthCredentials = {
      email,
      password,
      display_name: name,
      verification_code: verificationCode.trim() || undefined,
    };
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    try {
      const result = resetMode
        ? await resetPassword(
            { email, verification_code: verificationCode.trim(), new_password: password },
            controller.signal,
          )
        : await authenticate(effectiveMode, values, controller.signal, captchaToken);
      if (controller.signal.aborted) return;
      saveSession(result);
      setPassword("");
      setVerificationCode("");
      navigate("/workspace", { replace: true, state: { created: register } });
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error instanceof Error ? error.message : "暂时无法连接，请稍后重试。");
        setErrorCode(error instanceof AuthApiError ? error.code : "");
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function handleCaptchaSuccess(token: string) {
    setCaptchaOpen(false);
    if (pendingCaptchaAction === "send_code") {
      void executeSendCode(token);
    } else {
      void executeSubmit(token);
    }
  }

  async function logout() {
    if (!session || busy) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      await revokeSession(session.session.token, controller.signal);
      if (controller.signal.aborted) return;
      redirectingRef.current = true;
      navigate("/login", { replace: true, state: { notice: "已安全退出登录。" } });
      saveSession(null);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof AuthApiError && error.status === 401) {
        redirectingRef.current = true;
        navigate("/login", { replace: true });
        saveSession(null);
      } else setError(error instanceof Error ? error.message : "退出未完成，请稍后重试。");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  const showAccount = mode === "account" && session && verified;

  return (
    <main className="auth-page">
      <div className="auth-background" aria-hidden="true">
        <AuthVaultBackground />
        <div className="auth-background-shade" />
      </div>
      <header className="auth-page-header">
        <Link className="auth-brand" to="/" aria-label="舟序，返回首页">
          <img src="/zhou-xu-mark.png" alt="" width="34" height="34" />
          <span className="auth-wordmark" lang="en">Zhouxu<span aria-hidden="true">.</span></span>
          <span className="auth-brand-name">舟序</span>
        </Link>
        <Link className="auth-back" to="/"><ArrowLeft size={15} aria-hidden="true" />返回首页</Link>
      </header>

      <div className="auth-stage">
      <section className="auth-panel" aria-label={showAccount ? "我的账户" : register ? "注册账户" : "账户登录"}>
        <div className="auth-form-wrap">
          {checking ? (
            <div className="auth-session-loading" role="status"><LoaderCircle className="auth-spinner" size={22} /><p>正在确认登录状态…</p></div>
          ) : session && !verified ? (
            <div className="auth-session-retry">
              <p className="auth-eyebrow">YOUR ACCOUNT</p>
              <h1 className="font-editorial">再确认一下。</h1>
              <p className="auth-intro">暂时未能确认当前登录状态。恢复连接后，可以继续验证。</p>
              <button className="auth-submit" onClick={() => { setError(null); setRetry(value => value + 1); }}>重新连接<ArrowRight size={17} aria-hidden="true" /></button>
            </div>
          ) : showAccount ? (
            <div className="auth-success">
              <span className="auth-success-mark"><Check size={23} aria-hidden="true" /></span>
              <p className="auth-eyebrow">{location.state?.created ? "A NEW BEGINNING" : "GOOD TO SEE YOU"}</p>
              <h1 className="font-editorial">{location.state?.created ? "很高兴认识你。" : "欢迎回来。"}</h1>
              <p className="auth-intro">{location.state?.created ? "账户已创建，你已登录舟序。" : "你已登录舟序。"}</p>
              <form className="auth-account-edit" onSubmit={saveAccountDisplayName}>
                <dl className="auth-account-details">
                  <div>
                    <dt>称呼</dt>
                    <dd style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <input
                        aria-label="显示称呼"
                        type="text"
                        maxLength={80}
                        value={accountName}
                        onChange={(e) => { setAccountName(e.target.value); setAccountSaved(""); }}
                        style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(137,170,204,0.22)", borderRadius: "6px", padding: "4px 8px", color: "inherit", font: "inherit", minWidth: 0, flex: 1 }}
                      />
                      <button type="submit" disabled={busy} className="auth-inline-save" style={{ background: "rgba(137,170,204,0.16)", border: "1px solid rgba(137,170,204,0.32)", borderRadius: "6px", padding: "4px 10px", color: "inherit", fontSize: "12px", cursor: "pointer" }}>
                        保存
                      </button>
                    </dd>
                  </div>
                  <div><dt>邮箱</dt><dd>{session.account.email}</dd></div>
                </dl>
                {accountSaved && <p className="auth-notice" role="status">{accountSaved}</p>}
              </form>
              <Link className="auth-submit" to="/workspace">进入工作台<ArrowRight size={17} aria-hidden="true" /></Link>
              <Link className="auth-switch-copy" to="/workspace?settings=1" style={{ display: "inline-flex", justifyContent: "center", width: "100%", marginTop: "10px" }}>打开记忆、/ 指令与密码设置<ArrowRight size={12} aria-hidden="true" /></Link>
              <button className="auth-logout" disabled={busy} onClick={logout}>{busy ? <LoaderCircle className="auth-spinner" size={15} /> : <LogOut size={15} aria-hidden="true" />}{busy ? "正在退出…" : "退出登录"}</button>
            </div>
          ) : (
            <>
              <nav className="auth-mode-nav" aria-label="账户操作">
                <Link to="/login" aria-current={!register ? "page" : undefined} aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>登录</Link>
                <Link to="/register" aria-current={register ? "page" : undefined} aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>注册</Link>
              </nav>
              <div className="auth-form-heading">
                <h1 className="font-editorial">{register ? "创建舟序账户" : resetMode ? "重置密码" : "欢迎回来"}</h1>
                <p className="auth-intro">{register ? "填写邮箱，完成验证后即可开始。" : resetMode ? "通过邮箱验证码设置新密码。" : "登录后继续你的工作。"}</p>
              </div>
              {location.state?.notice && <p className="auth-notice" role="status">{location.state.notice}</p>}
              <form ref={formRef} onSubmit={submit} noValidate aria-busy={busy}>
                <fieldset disabled={busy} className="auth-fields">
                  {register && <div className="auth-field">
                    <label htmlFor="auth-name">怎么称呼你<span className="auth-optional">选填</span></label>
                    <input id="auth-name" name="display_name" type="text" autoComplete="nickname" maxLength={80} value={name} onChange={e => setName(e.target.value)} placeholder="你的称呼" />
                  </div>}
                  <div className="auth-field">
                    <label htmlFor="auth-email">邮箱地址</label>
                    <input id="auth-email" name="email" type="email" autoComplete="username" inputMode="email" autoCapitalize="none" spellCheck={false} required value={email} onChange={e => { setEmail(e.target.value); setFieldErrors(errors => ({ ...errors, email: undefined })); }} aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? "auth-email-error" : undefined} placeholder="name@example.com" />
                    {fieldErrors.email && <p id="auth-email-error" className="auth-field-error">{fieldErrors.email}</p>}
                  </div>
                  {(resetMode || (register && authConfig.email_verification_enabled)) && (
                    <div className="auth-field">
                      <label htmlFor="auth-code">邮箱验证码</label>
                      <div className="auth-code-row">
                        <input
                          id="auth-code"
                          name="verification_code"
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={6}
                          value={verificationCode}
                          onChange={e => {
                            setVerificationCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                            setFieldErrors(errors => ({ ...errors, verification_code: undefined }));
                          }}
                          aria-invalid={Boolean(fieldErrors.verification_code)}
                          aria-describedby={fieldErrors.verification_code ? "auth-code-error" : codeNotice ? "auth-code-hint" : undefined}
                          placeholder="6 位数字验证码"
                        />
                        <button
                          type="button"
                          className="auth-code-btn"
                          disabled={sendingCode || codeCooldown > 0}
                          onClick={() => void handleSendCode()}
                        >
                          {sendingCode ? "发送中…" : codeCooldown > 0 ? `${codeCooldown}s` : "发送验证码"}
                        </button>
                      </div>
                      {fieldErrors.verification_code
                        ? <p id="auth-code-error" className="auth-field-error">{fieldErrors.verification_code}</p>
                        : codeNotice && <p id="auth-code-hint" className="auth-field-hint">{codeNotice}</p>}
                    </div>
                  )}
                  <div className="auth-field">
                    <div className="auth-field-heading">
                      <label htmlFor="auth-password">{resetMode ? "新密码" : "密码"}</label>
                      {!register && !resetMode && authConfig.allow_password_reset && (
                        <button type="button" className="auth-link-btn" onClick={() => {
                          setResetMode(true);
                          setError(null);
                          setErrorCode("");
                          setCodeNotice("");
                          setFieldErrors({});
                        }}>忘记密码？</button>
                      )}
                    </div>
                    <div className="auth-password-wrap">
                      <input id="auth-password" name="password" type={reveal ? "text" : "password"} autoComplete={register || resetMode ? "new-password" : "current-password"} required value={password} onChange={e => { setPassword(e.target.value); setFieldErrors(errors => ({ ...errors, password: undefined })); }} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "auth-password-error" : (register || resetMode) ? "auth-password-hint" : undefined} placeholder={(register || resetMode) ? "设置至少 12 位密码" : "输入你的密码"} />
                      <button className="auth-reveal" type="button" aria-label={reveal ? "隐藏密码" : "显示密码"} aria-pressed={reveal} onClick={() => setReveal(value => !value)}>{reveal ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}</button>
                    </div>
                    {fieldErrors.password ? <p id="auth-password-error" className="auth-field-error">{fieldErrors.password}</p> : (register || resetMode) && <p id="auth-password-hint" className="auth-field-hint">至少 12 个字符，建议混合字母、数字与符号。</p>}
                  </div>
                  <button className="auth-submit" type="submit" disabled={busy}>
                    <span>{busy ? resetMode ? "正在重置密码…" : register ? "正在创建账户…" : "正在登录…" : resetMode ? "重置密码并登录" : register ? "创建账户" : "登录舟序"}</span>
                    {busy ? <LoaderCircle className="auth-spinner" size={17} aria-hidden="true" /> : <ArrowRight size={17} aria-hidden="true" />}
                  </button>
                </fieldset>
              </form>
              {resetMode ? (
                <div className="auth-secondary-links">
                  <button
                    type="button"
                    className="auth-link-btn"
                    onClick={() => {
                      setResetMode(false);
                      setError(null);
                      setErrorCode("");
                      setCodeNotice("");
                      setVerificationCode("");
                      setFieldErrors({});
                    }}
                  >
                    返回登录
                  </button>
                </div>
              ) : <div className="auth-secondary-links"><Link className="auth-admin-link" to="/admin/login">管理员登录<ArrowRight size={13} aria-hidden="true" /></Link></div>}
            </>
          )}

          {error && <div className="auth-error" ref={alertRef} role="alert" tabIndex={-1}>
            <p>{error}</p>
            {errorCode === "account_email_exists" && <Link to="/login" state={{ email }}>使用这个邮箱登录<ArrowRight size={13} aria-hidden="true" /></Link>}
          </div>}
        </div>

      </section>
      </div>
      <footer className="auth-page-footer">
        <p className="font-editorial">让事实有序，让判断有据。</p>
        <span lang="en">RESEARCH <span className="auth-dot">/</span> REASON <span className="auth-dot">/</span> RESOLVE</span>
      </footer>

      <CaptchaSliderModal
        open={captchaOpen}
        onClose={() => setCaptchaOpen(false)}
        onSuccess={handleCaptchaSuccess}
      />
    </main>
  );
}
