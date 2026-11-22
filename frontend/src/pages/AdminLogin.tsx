import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Eye, EyeOff, LoaderCircle, ShieldCheck } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import AuthVaultBackground from "@/components/AuthVaultBackground";
import { authenticateAdmin, AuthApiError, validateCredentials, type AuthCredentials } from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
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

  useEffect(() => {
    if (session?.account.role === "admin") navigate("/admin/overview", { replace: true });
  }, [navigate, session]);

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
    <main className="admin-login-page">
      <div className="admin-login-bg" aria-hidden="true"><AuthVaultBackground /><div /></div>
      <header className="admin-login-header">
        <Link className="admin-login-brand" to="/" aria-label="舟序，返回首页">
          <img src="/zhou-xu-mark.png" alt="" width="32" height="32" />
          <span>Zhouxu<span aria-hidden="true">.</span></span><b>舟序</b>
        </Link>
        <Link className="admin-login-back" to="/login"><ArrowLeft size={15} aria-hidden="true" />普通登录</Link>
      </header>
      <section className="admin-login-panel" aria-labelledby="admin-login-title">
        <p className="admin-kicker"><ShieldCheck size={15} aria-hidden="true" />管理员入口</p>
        <h1 id="admin-login-title">进入管理台</h1>
        <p className="admin-login-intro">管理账户与业务记录。</p>
        {typeof location.state?.notice === "string" && <p className="admin-login-notice" role="status">{location.state.notice}</p>}
        <form ref={formRef} onSubmit={submit} noValidate aria-busy={busy}>
          <fieldset disabled={busy}>
            <label className="admin-field" htmlFor="admin-email"><span>邮箱</span><input id="admin-email" name="email" type="email" autoComplete="username" value={email} onChange={event => { setEmail(event.target.value); setFieldErrors(current => ({ ...current, email: undefined })); }} placeholder="admin@example.com" aria-invalid={Boolean(fieldErrors.email)} /></label>
            {fieldErrors.email && <p className="admin-field-error">{fieldErrors.email}</p>}
            <label className="admin-field" htmlFor="admin-password"><span>密码</span><span className="admin-password-wrap"><input id="admin-password" name="password" type={reveal ? "text" : "password"} autoComplete="current-password" value={password} onChange={event => { setPassword(event.target.value); setFieldErrors(current => ({ ...current, password: undefined })); }} placeholder="输入密码" aria-invalid={Boolean(fieldErrors.password)} /><button type="button" onClick={() => setReveal(value => !value)} aria-label={reveal ? "隐藏密码" : "显示密码"}>{reveal ? <EyeOff size={17} /> : <Eye size={17} />}</button></span></label>
            {fieldErrors.password && <p className="admin-field-error">{fieldErrors.password}</p>}
            <button className="admin-submit" type="submit"><span>{busy ? "正在登录…" : "登录管理台"}</span>{busy ? <LoaderCircle className="admin-spin" size={17} /> : <ArrowRight size={17} />}</button>
          </fieldset>
        </form>
        {error && <p className="admin-login-error" role="alert">{error}</p>}
        <p className="admin-login-note">普通账户请从<Link to="/login">普通登录</Link>进入。</p>
      </section>
    </main>
  );
}
