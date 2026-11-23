import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Eye, EyeOff, LoaderCircle, ShieldCheck } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import { authenticateAdmin, AuthApiError, validateCredentials, type AuthCredentials } from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
import "./auth.css";
import "./admin.css";

export default function AdminLogin() {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useAuthSession();
  const [email, setEmail] = useState(location.state?.email || "");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof AuthCredentials, string>>>({});
  const formRef = useRef<HTMLFormElement>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (session?.account.role === "admin") navigate("/admin/overview", { replace: true });
  }, [navigate, session]);

  useEffect(() => { if (error) alertRef.current?.focus(); }, [error]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError(null);
    const errors = validateCredentials("login", { email, password });
    setFieldErrors(errors);
    const firstError = Object.keys(errors)[0];
    if (firstError) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${firstError}"]`)?.focus();
      return;
    }
    setBusy(true);
    try {
      const result = await authenticateAdmin({ email, password });
      saveSession(result);
      setPassword("");
      navigate("/admin/overview", { replace: true });
    } catch (cause) {
      setError(cause instanceof AuthApiError ? cause.message : "暂时无法登录，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page admin-page">
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
        <Link className="auth-back" to="/login"><ArrowLeft size={15} aria-hidden="true" />普通登录</Link>
      </header>

      <div className="auth-stage">
        <section className="auth-panel" aria-labelledby="admin-login-title">
          <div className="auth-form-wrap">
            <div className="auth-form-heading">
              <p className="admin-kicker"><ShieldCheck size={15} aria-hidden="true" />管理员入口</p>
              <h1 id="admin-login-title" className="font-editorial">进入管理台</h1>
              <p className="auth-intro">管理账户与业务记录。</p>
            </div>

            {typeof location.state?.notice === "string" && (
              <p className="auth-notice" role="status">{location.state.notice}</p>
            )}

            <form ref={formRef} onSubmit={submit} noValidate aria-busy={busy}>
              <fieldset disabled={busy} className="auth-fields">
                <div className="auth-field">
                  <label htmlFor="admin-email">邮箱地址</label>
                  <input
                    id="admin-email"
                    name="email"
                    type="email"
                    autoComplete="username"
                    inputMode="email"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={email}
                    onChange={e => { setEmail(e.target.value); setFieldErrors(c => ({ ...c, email: undefined })); }}
                    placeholder="admin@example.com"
                    aria-invalid={Boolean(fieldErrors.email)}
                    aria-describedby={fieldErrors.email ? "admin-email-error" : undefined}
                  />
                  {fieldErrors.email && <p id="admin-email-error" className="auth-field-error">{fieldErrors.email}</p>}
                </div>
                <div className="auth-field">
                  <label htmlFor="admin-password">密码</label>
                  <div className="auth-password-wrap">
                    <input
                      id="admin-password"
                      name="password"
                      type={reveal ? "text" : "password"}
                      autoComplete="current-password"
                      value={password}
                      onChange={e => { setPassword(e.target.value); setFieldErrors(c => ({ ...c, password: undefined })); }}
                      placeholder="输入密码"
                      aria-invalid={Boolean(fieldErrors.password)}
                      aria-describedby={fieldErrors.password ? "admin-password-error" : undefined}
                    />
                    <button className="auth-reveal" type="button" onClick={() => setReveal(v => !v)} aria-label={reveal ? "隐藏密码" : "显示密码"} aria-pressed={reveal}>
                      {reveal ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}
                    </button>
                  </div>
                  {fieldErrors.password && <p id="admin-password-error" className="auth-field-error">{fieldErrors.password}</p>}
                </div>
                <button className="auth-submit" type="submit" disabled={busy}>
                  <span>{busy ? "正在登录…" : "登录管理台"}</span>
                  {busy ? <LoaderCircle className="auth-spinner" size={17} aria-hidden="true" /> : <ArrowRight size={17} aria-hidden="true" />}
                </button>
              </fieldset>
            </form>

            {error && (
              <p className="auth-error" ref={alertRef} role="alert" tabIndex={-1}>{error}</p>
            )}

            <p className="auth-panel-assurance">
              <ShieldCheck size={14} aria-hidden="true" />
              <span>普通账户请从<Link to="/login">普通登录</Link>进入。</span>
            </p>
          </div>
        </section>
      </div>

      <footer className="auth-page-footer">
        <p className="font-editorial">让事实有序，让判断有据。</p>
        <span lang="en">RESEARCH <span className="auth-dot">/</span> REASON <span className="auth-dot">/</span> RESOLVE</span>
      </footer>
    </main>
  );
}
