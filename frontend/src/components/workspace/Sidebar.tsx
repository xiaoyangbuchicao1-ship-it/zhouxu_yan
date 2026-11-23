import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Plus,
  Library,
  PanelLeftClose,
  PanelLeftOpen,
  Folder,
  FolderOpen,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Pin,
  PinOff,
  Trash2,
  LogOut,
  BriefcaseBusiness,
  Compass,
  MessageCircle,
  Search,
  Settings,
} from "lucide-react";
import logo from "../../../zhou-xu-mark.png";
import { IconButton, Menu, MenuItem, Loading, LoadError } from "./Primitives";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { AuthSession } from "@/lib/auth";
import type { Conversation } from "@/lib/workbench-api";
import { isUntitledConversation } from "@/lib/conversation-title";
import type { UserCenterTab } from "./UserCenterDialog";

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
  onOpenUserCenter?: (tab?: UserCenterTab) => void;
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
  onOpenUserCenter,
  logout,
}: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const officePage = location.pathname.match(/^\/workspace\/(skills|research)\/?$/)?.[1];
  const conversations = ws.history.data?.conversations || [];
  const activeChat = id ? conversations.find((c) => c.id === id) : undefined;
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({});
  const isProjectOpen = (projectId: string) => {
    if (projectId in expandedProjects) return expandedProjects[projectId];
    return ws.projectFilter === projectId || activeChat?.project_id === projectId;
  };
  const standalone = conversations.filter((c) => !c.project_id);
  const pinned = standalone.filter((c) => c.pinned);
  const recent = standalone.filter((c) => !c.pinned);
  const row = (chat: Conversation, insideProject = false) => {
    const title = isUntitledConversation(chat.title) ? "待提问" : chat.title;
    const ModeIcon = chat.mode === "work" ? BriefcaseBusiness : MessageCircle;
    const modeLabel = chat.mode === "work" ? "工作会话" : "聊天会话";
    return (
    <div
      key={chat.id}
      className={`ws-history-row ${insideProject ? "is-nested" : ""} ${id === chat.id ? "is-selected" : ""}`}
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
        onClick={() => {
          if (!insideProject) ws.setProjectFilter(null);
          else if (chat.project_id) ws.setProjectFilter(chat.project_id);
          onNavigate();
        }}
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
          ["skills", "技能", Compass],
          ["research", "海事检索", Search],
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
                <LoadError compact label="项目载入失败" retry={() => void ws.projects.refetch()} />
              ) : (
                <>
                  {(ws.projects.data?.projects || []).map((project) => {
                    const projectChats = conversations
                      .filter((c) => c.project_id === project.id)
                      .sort((a, b) => Number(b.pinned) - Number(a.pinned));
                    const open = isProjectOpen(project.id);
                    const selected = ws.projectFilter === project.id;
                    const count = ws.history.data ? projectChats.length : project.conversation_count;
                    const FolderIcon = open ? FolderOpen : Folder;
                    return (
                    <div className="ws-project-branch" key={project.id}>
                      <div
                        className={`ws-project-row ${selected ? "is-selected" : ""}`}
                      >
                        <button
                          type="button"
                          className="ws-project-link"
                          onClick={() => {
                            if (selected) {
                              ws.setProjectFilter(null);
                              setExpandedProjects((prev) => ({ ...prev, [project.id]: false }));
                            } else {
                              ws.setProjectFilter(project.id);
                              setExpandedProjects((prev) => ({ ...prev, [project.id]: true }));
                              if (location.pathname !== "/workspace") navigate("/workspace");
                              onNavigate();
                            }
                          }}
                          aria-expanded={open}
                          aria-pressed={selected}
                        >
                          <span className="ws-project-chevron" aria-hidden="true">
                            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </span>
                          <FolderIcon size={15} />
                          <span className="ws-project-name">{project.name}</span>
                          <span className="ws-count">{count}</span>
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
                      {open && projectChats.length > 0 && (
                        <div className="ws-project-children" role="group" aria-label={`${project.name} 中的对话`}>
                          {projectChats.map((chat) => row(chat, true))}
                        </div>
                      )}
                    </div>
                    );
                  })}
                  {!ws.projects.data?.projects.length && (
                    <p className="ws-sidebar-hint">将相关对话，归于一处。</p>
                  )}
                </>
              )}
            </section>
            <section className="ws-history" aria-label="历史对话">
              {ws.history.isPending ? (
                <Loading label="载入对话…" />
              ) : ws.history.isError ? (
                <LoadError compact label="对话载入失败" retry={() => void ws.history.refetch()} />
              ) : (
                <>
                  {pinned.length > 0 && (
                    <div className="ws-history-group">
                      <h3>
                        置顶
                        <Pin size={14} aria-hidden="true" />
                      </h3>
                      {pinned.map((chat) => row(chat, false))}
                    </div>
                  )}
                  {recent.length > 0 && (
                    <div className="ws-history-group">
                      <h3>最近</h3>
                      {recent.map((chat) => row(chat, false))}
                    </div>
                  )}
                  {!conversations.length && !ws.projects.data?.projects.length && (
                    <p className="ws-sidebar-hint">
                      从一个问题开始，思路会留在这里。
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
                </span>
                <span className="ws-account-subline">
                  <span>个人工作空间</span>
                  <span className="ws-account-dot" aria-hidden="true">·</span>
                  <span className="ws-beta-tag">内测版</span>
                </span>
              </span>
              <ChevronDown size={16} />
            </button>
          }
        >
          <div className="ws-menu-caption">{session.account.email}</div>
          {onOpenUserCenter && (
            <MenuItem onSelect={() => onOpenUserCenter("account")}>
              <Settings size={16} />
              设置
            </MenuItem>
          )}
          <MenuItem onSelect={logout}>
            <LogOut size={16} />
            退出登录
          </MenuItem>
        </Menu>
      </div>
    </div>
  );
}

