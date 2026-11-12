import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, LoaderCircle, LogOut, ShieldCheck } from "lucide-react";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import { authenticate, AuthApiError, revokeSession, validateCredentials, verifySession, type AuthCredentials, type AuthMode } from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
import "./auth.css";

export default function AuthPage({ mode = "login" }: { mode?: AuthMode | "account" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useAuthSession();
  const register = mode === "register";
  const [email, setEmail] = useState(location.state?.email || "");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(Boolean(session));
  const [verified, setVerified] = useState(false);
  const [retry, setRetry] = useState(0);
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
    document.title = `${mode === "account" ? "我的账户" : register ? "注册" : "登录"} · 舟序 Zhouxu`;
    setPassword("");
    setReveal(false);
    setFieldErrors({});
    setError(null);
    setErrorCode("");
    setBusy(false);
    redirectingRef.current = false;
    return () => { requestRef.current?.abort(); document.title = previousTitle; };
  }, [mode, register]);

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

  useEffect(() => { if (error) alertRef.current?.focus(); }, [error]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || mode === "account") return;
    setError(null);
    setErrorCode("");
    const values = { email, password, display_name: name };
    const errors = validateCredentials(mode, values);
    setFieldErrors(errors);
    const firstError = Object.keys(errors)[0];
    if (firstError) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${firstError}"]`)?.focus();
      return;
    }
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    try {
      const result = await authenticate(mode, values, controller.signal);
      if (controller.signal.aborted) return;
      saveSession(result);
      setPassword("");
      navigate("/workspace", { replace: true, state: { created: register } });
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error instanceof Error ? error.message : "暂时无法连接，请稍后重试。");
        setErrorCode(error instanceof AuthApiError ? error.code : "");
      }
    } finally { if (!controller.signal.aborted) setBusy(false); }
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
              <dl className="auth-account-details">
                <div><dt>称呼</dt><dd>{session.account.display_name}</dd></div>
                <div><dt>邮箱</dt><dd>{session.account.email}</dd></div>
              </dl>
              <Link className="auth-submit" to="/workspace">进入工作台<ArrowRight size={17} aria-hidden="true" /></Link>
              <button className="auth-logout" disabled={busy} onClick={logout}>{busy ? <LoaderCircle className="auth-spinner" size={15} /> : <LogOut size={15} aria-hidden="true" />}{busy ? "正在退出…" : "退出登录"}</button>
            </div>
          ) : (
            <>
              <nav className="auth-mode-nav" aria-label="账户操作">
                <Link to="/login" aria-current={!register ? "page" : undefined} aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>登录</Link>
                <Link to="/register" aria-current={register ? "page" : undefined} aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>注册</Link>
              </nav>
              <div className="auth-form-heading">
                <h1 className="font-editorial">{register ? "从这里，开启舟序。" : "欢迎回到舟序。"}</h1>
                <p className="auth-intro">{register ? "建立你的账户，让每一次研判有迹可循。" : "登录你的账户，接续每一次思考。"}</p>
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
                  <div className="auth-field">
                    <label htmlFor="auth-password">密码</label>
                    <div className="auth-password-wrap">
                      <input id="auth-password" name="password" type={reveal ? "text" : "password"} autoComplete={register ? "new-password" : "current-password"} required value={password} onChange={e => { setPassword(e.target.value); setFieldErrors(errors => ({ ...errors, password: undefined })); }} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "auth-password-error" : register ? "auth-password-hint" : undefined} placeholder={register ? "创建至少 12 位密码" : "输入你的密码"} />
                      <button className="auth-reveal" type="button" aria-label={reveal ? "隐藏密码" : "显示密码"} aria-pressed={reveal} onClick={() => setReveal(value => !value)}>{reveal ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}</button>
                    </div>
                    {fieldErrors.password ? <p id="auth-password-error" className="auth-field-error">{fieldErrors.password}</p> : register && <p id="auth-password-hint" className="auth-field-hint">至少 12 个字符，建议混合字母、数字与符号。</p>}
                  </div>
                  <button className="auth-submit" type="submit" disabled={busy}>
                    <span>{busy ? register ? "正在创建账户…" : "正在登录…" : register ? "创建账户" : "登录舟序"}</span>
                    {busy ? <LoaderCircle className="auth-spinner" size={17} aria-hidden="true" /> : <ArrowRight size={17} aria-hidden="true" />}
                  </button>
                </fieldset>
              </form>
              <p className="auth-switch-copy">{register ? "已有账户？" : "初次来到舟序？"}<Link to={register ? "/login" : "/register"} state={{ email }} aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>{register ? "直接登录" : "创建一个账户"}<ArrowRight size={12} aria-hidden="true" /></Link></p>
            </>
          )}

          {error && <div className="auth-error" ref={alertRef} role="alert" tabIndex={-1}>
            <p>{error}</p>
            {errorCode === "account_email_exists" && <Link to="/login" state={{ email }}>使用这个邮箱登录<ArrowRight size={13} aria-hidden="true" /></Link>}
          </div>}
        </div>

        <p className="auth-panel-assurance"><ShieldCheck size={14} aria-hidden="true" /><span>让专业判断，始终由你掌握。</span></p>
      </section>
      </div>
      <footer className="auth-page-footer">
        <p className="font-editorial">让事实有序，让判断有据。</p>
        <span lang="en">RESEARCH <span className="auth-dot">/</span> REASON <span className="auth-dot">/</span> RESOLVE</span>
      </footer>
    </main>
  );
}
