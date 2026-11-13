import { Link, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Plus,
  Library,
  PanelLeftClose,
  PanelLeftOpen,
  Folder,
  ChevronDown,
  MoreHorizontal,
  Pin,
  PinOff,
  Trash2,
  LogOut,
  ArrowUpRight,
  BriefcaseBusiness,
  Compass,
  MessageCircle,
  Search,
} from "lucide-react";
import logo from "../../../zhou-xu-mark.png";
import { IconButton, Menu, MenuItem, Loading, LoadError } from "./Primitives";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { AuthSession } from "@/lib/auth";
import type { Conversation } from "@/lib/workbench-api";
import { isUntitledConversation } from "@/lib/conversation-title";

type Workspace = ReturnType<typeof useWorkspace>;
export interface SidebarProps {
  ws: Workspace;
  session: AuthSession;
  id?: string;
  library: boolean;
  collapsed: boolean;
  toggle: () => void;
  onNavigate: () => void;
  newProject: () => void;
  deleteProject: (id: string, name: string) => void;
  deleteChat: (chat: Conversation) => void;
  moveChat: (chat: Conversation) => void;
  logout: () => void;
}

export default function Sidebar({
  ws,
  session,
  id,
  library,
  collapsed,
  toggle,
  onNavigate,
  newProject,
  deleteProject,
  deleteChat,
  moveChat,
  logout,
}: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const officePage = location.pathname.match(/^\/workspace\/(skills|research|cases)\/?$/)?.[1];
  const conversations = ws.history.data?.conversations || [];
  const list = ws.projectFilter
    ? conversations.filter((c) => c.project_id === ws.projectFilter)
    : conversations;
  const pinned = list.filter((c) => c.pinned);
  const recent = list.filter((c) => !c.pinned);
  const row = (chat: Conversation) => {
    const title = isUntitledConversation(chat.title) ? "待提问" : chat.title;
    const ModeIcon = chat.mode === "work" ? BriefcaseBusiness : MessageCircle;
    const modeLabel = chat.mode === "work" ? "工作会话" : "聊天会话";
    return (
    <div
      key={chat.id}
      className={`ws-history-row ${id === chat.id ? "is-selected" : ""}`}
    >
      {id === chat.id && (
        <motion.span
          layoutId="ws-active-chat"
          className="ws-active-background"
          transition={{ type: "spring", stiffness: 500, damping: 40 }}
        />
      )}
      <Link
        to={`/workspace/chat/${chat.id}`}
        className="ws-history-link"
        title={title}
        onClick={onNavigate}
        aria-current={id === chat.id ? "page" : undefined}
      >
        <span>{title}</span>
        <span className={`ws-history-mode ws-history-mode-${chat.mode}`} aria-label={modeLabel} title={modeLabel}>
          <ModeIcon size={12} strokeWidth={1.8} aria-hidden="true" />
        </span>
        {ws.turn?.conversationId === chat.id && ws.turn.state === "receiving" && (
          <span className="ws-receiving-dot" aria-label="正在接收回答" />
        )}
      </Link>
      <Menu
        className="ws-history-menu"
        trigger={
          <IconButton label={`${title}，更多操作`}>
            <MoreHorizontal size={17} />
          </IconButton>
        }
      >
        <MenuItem
          onSelect={() => void ws.act(() => ws.api.pin(chat.id, !chat.pinned))}
        >
          {chat.pinned ? <PinOff size={16} /> : <Pin size={16} />}
          {chat.pinned ? "取消置顶" : "置顶对话"}
        </MenuItem>
        <MenuItem onSelect={() => moveChat(chat)}>
          <Folder size={16} />
          移动到项目
        </MenuItem>
        <div className="ws-history-menu-divider" role="separator" />
        <MenuItem
          onSelect={() => deleteChat(chat)}
          danger
        >
          <Trash2 size={16} />
          删除对话
        </MenuItem>
      </Menu>
    </div>
    );
  };

  return (
    <div className={`ws-sidebar-content ${collapsed ? "is-collapsed" : ""}`}>
      <div className="ws-brand-row">
        <Link to="/" aria-label="舟序首页" className="ws-brand">
          <img src={logo} alt="" />
          <span>Zhouxu.</span>
        </Link>
        <IconButton
          label={collapsed ? "展开侧栏" : "收起侧栏"}
          onClick={toggle}
        >
          {collapsed ? (
            <PanelLeftOpen size={19} />
          ) : (
            <PanelLeftClose size={19} />
          )}
        </IconButton>
      </div>
      <nav className="ws-primary-nav" aria-label="工作台导航">
        <Link
          to="/workspace"
          onClick={onNavigate}
          className={`ws-new-chat ${!id && !library && !officePage ? "is-active" : ""}`}
          title="新建对话"
        >
          <Plus size={19} />
          <span>新建对话</span>
        </Link>
        <Link
          to="/workspace/library"
          onClick={onNavigate}
          className={`ws-nav-link ${library ? "is-active" : ""}`}
          title="资料库"
          aria-current={library ? "page" : undefined}
        >
          <Library size={18} />
          <span>资料库</span>
        </Link>
        {([
          ["skills", "技能工坊", Compass],
          ["research", "海事检索", Search],
          ["cases", "案件协作", BriefcaseBusiness],
        ] as const).map(([path, label, Icon]) => (
          <Link
            key={path}
            to={`/workspace/${path}`}
            onClick={onNavigate}
            className={`ws-nav-link ${officePage === path ? "is-active" : ""}`}
            title={label}
            aria-current={officePage === path ? "page" : undefined}
          >
            <Icon size={18} />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            className="ws-sidebar-body"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14 }}
          >
            <section className="ws-projects" aria-label="项目分组">
              <div className="ws-section-label">
                <h2>项目</h2>
                <IconButton label="新建项目" onClick={newProject}>
                  <Plus size={16} />
                </IconButton>
              </div>
              {ws.projects.isPending ? (
                <Loading label="载入项目…" />
              ) : ws.projects.isError ? (
                <LoadError retry={() => void ws.projects.refetch()} />
              ) : (
                <>
                  {(ws.projects.data?.projects || []).map((project) => (
                    <div
                      className={`ws-project-row ${ws.projectFilter === project.id ? "is-selected" : ""}`}
                      key={project.id}
                    >
                      <button
                        className="ws-project-link"
                        onClick={() =>
                          ws.setProjectFilter(
                            ws.projectFilter === project.id ? null : project.id,
                          )
                        }
                        aria-pressed={ws.projectFilter === project.id}
                      >
                        <Folder size={16} />
                        <span>{project.name}</span>
                        <span className="ws-count">
                          {project.conversation_count}
                        </span>
                      </button>
                      <Menu
                        trigger={
                          <IconButton label={`${project.name}，项目操作`}>
                            <MoreHorizontal size={16} />
                          </IconButton>
                        }
                      >
                        <MenuItem
                          danger
                          disabled={ws.busy}
                          onSelect={() =>
                            deleteProject(project.id, project.name)
                          }
                        >
                          <Trash2 size={16} />
                          删除分组
                        </MenuItem>
                      </Menu>
                    </div>
                  ))}
                  {!ws.projects.data?.projects.length && (
                    <p className="ws-sidebar-hint">将相关对话，归于一处。</p>
                  )}
                </>
              )}
            </section>
            <section className="ws-history" aria-label="历史对话">
              {ws.projectFilter && (
                <div className="ws-section-label">
                  <h2>项目中的对话</h2>
                  <button
                    className="ws-text-button"
                    onClick={() => ws.setProjectFilter(null)}
                  >
                    全部
                  </button>
                </div>
              )}
              {ws.history.isPending ? (
                <Loading label="载入对话…" />
              ) : ws.history.isError ? (
                <LoadError retry={() => void ws.history.refetch()} />
              ) : (
                <>
                  {pinned.length > 0 && (
                    <div className="ws-history-group">
                      <h3>
                        置顶
                        <Pin size={14} aria-hidden="true" />
                      </h3>
                      {pinned.map(row)}
                    </div>
                  )}
                  {recent.length > 0 && (
                    <div className="ws-history-group">
                      <h3>最近</h3>
                      {recent.map(row)}
                    </div>
                  )}
                  {!list.length && (
                    <p className="ws-sidebar-hint">
                      {ws.projectFilter
                        ? "这个项目还没有对话。新对话将归入此项目。"
                        : "从一个问题开始，思路会留在这里。"}
                    </p>
                  )}
                </>
              )}
            </section>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="ws-account">
        <Menu
          side="top"
          align="start"
          trigger={
            <button
              className="ws-account-trigger"
              title={session.account.email}
            >
              <span className="ws-avatar">
                {(session.account.display_name || session.account.email)
                  .slice(0, 1)
                  .toUpperCase()}
              </span>
              <span className="ws-account-text">
                <span className="ws-account-name-line">
                  <strong>{session.account.display_name || "我的账户"}</strong>
                  <small className="ws-beta-badge">（内测中）</small>
                </span>
                <span>个人工作空间</span>
              </span>
              <ChevronDown size={16} />
            </button>
          }
        >
          <div className="ws-menu-caption">{session.account.email}</div>
          <MenuItem onSelect={() => navigate("/account")}>
            <ArrowUpRight size={16} />
            账户信息
          </MenuItem>
          <MenuItem onSelect={logout}>
            <LogOut size={16} />
            退出登录
          </MenuItem>
        </Menu>
      </div>
    </div>
  );
}

