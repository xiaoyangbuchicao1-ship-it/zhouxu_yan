import { ArrowLeft, Bot, Database, History, LayoutDashboard, LogOut, Menu, ShieldCheck, Sparkles, Users, Wrench } from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { revokeSession } from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
import { adminNavigationGroups } from "./admin-navigation";
import "../admin.css";

interface AdminOutletValue {
  token: string;
  ownAccountId: string;
}

const AdminOutletContext = createContext<AdminOutletValue | null>(null);

export function useAdminOutlet(): AdminOutletValue {
  const value = useContext(AdminOutletContext);
  if (!value) throw new Error("admin_outlet_missing");
  return value;
}

const icons: Record<string, ReactNode> = {
  "/admin/overview": <LayoutDashboard size={17} />,
  "/admin/accounts": <Users size={17} />,
  "/admin/models": <Bot size={17} />,
  "/admin/skills": <Sparkles size={17} />,
  "/admin/tools": <Wrench size={17} />,
  "/admin/data": <Database size={17} />,
  "/admin/audit": <History size={17} />,
};

function Navigation({ mobile = false }: { mobile?: boolean }) {
  const location = useLocation();
  return (
    <nav className={mobile ? "admin-mobile-nav" : "admin-rail-nav"} aria-label="管理台">
      {adminNavigationGroups.map(group => (
        <div className="admin-nav-group" key={group.label}>
          <span className="admin-nav-heading">{group.label}</span>
          {group.items.map(item => {
            const active = item.end
              ? location.pathname === item.to
              : location.pathname === item.to || location.pathname.startsWith(`${item.to}/`);
            const link = (
              <NavLink key={item.to} to={item.to} className={active ? "is-active" : ""} end={item.end}>
                {icons[item.to]}<span>{item.label}</span>
              </NavLink>
            );
            return mobile ? <SheetClose asChild key={item.to}>{link}</SheetClose> : link;
          })}
        </div>
      ))}
    </nav>
  );
}

export default function AdminLayout() {
  const session = useAuthSession();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);
  if (!session) return <Navigate to="/admin/login" replace />;
  if (session.account.role !== "admin") {
    return <Navigate to="/admin/login" replace state={{ notice: "当前账户没有管理员权限。" }} />;
  }

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try { await revokeSession(session.session.token); }
    catch (cause) {
      if (!(cause instanceof Error && "status" in cause && cause.status === 401)) {
        setLoggingOut(false);
        return;
      }
    }
    saveSession(null);
    navigate("/admin/login", { replace: true });
  }

  return (
    <main className="admin-page admin-shell">
      <aside className="admin-rail">
        <Link className="admin-shell-brand" to="/admin/overview" aria-label="舟序管理台">
          <img src="/zhou-xu-mark.png" alt="" width="34" height="34" />
          <span><strong>Zhouxu.</strong><small>管理台</small></span>
        </Link>
        <Navigation />
        <Link className="admin-rail-return" to="/workspace"><ArrowLeft size={16} />返回工作台</Link>
      </aside>

      <section className="admin-shell-main">
        <header className="admin-shell-header">
          <div className="admin-mobile-menu">
            <Sheet>
              <SheetTrigger asChild><button type="button" aria-label="打开导航"><Menu size={20} /></button></SheetTrigger>
              <SheetContent side="left" className="admin-mobile-sheet">
                <SheetTitle className="admin-mobile-sheet-title">舟序管理台</SheetTitle>
                <Navigation mobile />
                <SheetClose asChild><Link className="admin-mobile-return" to="/workspace"><ArrowLeft size={16} />返回工作台</Link></SheetClose>
              </SheetContent>
            </Sheet>
          </div>
          <div className="admin-header-actions">
            <span className="admin-identity"><ShieldCheck size={15} />{session.account.display_name || session.account.email}</span>
            <button className="admin-icon-button" type="button" onClick={logout} disabled={loggingOut} aria-label="退出登录"><LogOut size={17} /></button>
          </div>
        </header>
        <div className="admin-route-frame">
          <AdminOutletContext.Provider value={{ token: session.session.token, ownAccountId: session.account.id }}>
            <Outlet />
          </AdminOutletContext.Provider>
        </div>
      </section>
    </main>
  );
}

