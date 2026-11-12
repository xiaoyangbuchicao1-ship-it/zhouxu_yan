import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  AnimatePresence,
  motion,
  MotionConfig,
  useReducedMotion,
} from "framer-motion";
import {
  PanelRightOpen,
  PanelRightClose,
  Menu as MenuIcon,
  X,
  ChevronRight,
  Folder,
  ArrowLeft,
  Plus,
} from "lucide-react";
import { useAuthSession, saveSession, getSignedOutNotice } from "@/lib/auth-session";
import { revokeSession, type AuthSession } from "@/lib/auth";
import { useWorkspace } from "@/hooks/use-workspace";
import type { Conversation as Chat, Resource } from "@/lib/workbench-api";
import Sidebar, { type SidebarProps } from "@/components/workspace/Sidebar";
import Conversation from "@/components/workspace/Conversation";
import ConversationModeSwitch from "@/components/workspace/ConversationModeSwitch";
import {
  LibraryPage,
  MaterialsPanel,
  ResourcePreview,
} from "@/components/workspace/Resources";
import { Modal, IconButton } from "@/components/workspace/Primitives";
import "./workspace.css";

interface Confirmation {
  title: string;
  description: string;
  action: string;
  run: () => Promise<unknown>;
}

function Desk({ session }: { session: AuthSession }) {
  const navigate = useNavigate();
  const location = useLocation();
  const library = location.pathname === "/workspace/library";
  const match = location.pathname.match(/^\/workspace\/chat\/([^/]+)\/?$/);
  const id = match?.[1];
  const ws = useWorkspace(session, id);
  const reduced = useReducedMotion();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [materials, setMaterials] = useState(false);
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 1100px)").matches,
  );
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [projectModal, setProjectModal] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [moving, setMoving] = useState<Chat | null>(null);
  const [targetProject, setTargetProject] = useState("");
  const [preview, setPreview] = useState<Resource | null>(null);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    const change = () => setNarrow(media.matches);
    media.addEventListener("change", change);
    const originalOverflow = document.body.style.overflow;
    const originalTitle = document.title;
    document.body.style.overflow = "hidden";
    document.title = "舟序 Zhouxu｜研究工作台";
    return () => {
      media.removeEventListener("change", change);
      document.body.style.overflow = originalOverflow;
      document.title = originalTitle;
    };
  }, []);

  function deleteChat(chat: Chat) {
    setMobileNav(false);
    setConfirmation({
      title: "删除这段对话？",
      description: `“${chat.title}”及其消息会被永久删除，进行中的任务会停止。资料库原始文件保留。`,
      action: "删除对话",
      run: async () => {
        await ws.deleteConversation(chat.id);
        if (id === chat.id) navigate("/workspace", { replace: true });
      },
    });
  }
  function deleteProject(projectId: string, name: string) {
    setConfirmation({
      title: "删除项目分组？",
      description: `只删除“${name}”分组，其中的对话会保留在全部对话中，不会被删除。`,
      action: "删除分组",
      run: async () => {
        await ws.api.deleteProject(projectId);
        if (ws.projectFilter === projectId) ws.setProjectFilter(null);
      },
    });
  }
  function deleteResource(resource: Resource) {
    setConfirmation({
      title: "删除这份资料？",
      description: `“${resource.filename}”将从资料库删除，并解除 ${resource.conversation_count} 个对话中的关联。已保存的回答文字不会被改写。此操作无法恢复。`,
      action: "删除资料",
      run: () => ws.api.deleteResource(resource.id),
    });
  }
  const sidebarProps: SidebarProps = {
    ws,
    session,
    id,
    library,
    collapsed,
    toggle: () => setCollapsed((v) => !v),
    onNavigate: () => setMobileNav(false),
    newProject: () => {
      setProjectName("");
      setProjectModal(true);
    },
    deleteProject,
    deleteChat,
    moveChat: (chat) => {
      setMoving(chat);
      setTargetProject(chat.project_id || "");
    },
    logout: () => {
      void ws.act(async () => {
        await revokeSession(session.session.token);
        saveSession(null, "已安全退出登录。");
        navigate("/login", {
          replace: true,
          state: { notice: "已安全退出登录。" },
        });
      });
    },
  };
  const title = library
    ? "资料库"
    : ws.detail.data?.conversation.title || "新对话";
  const group = ws.projects.data?.projects.find(
    (p) =>
      p.id ===
      (ws.detail.data?.conversation.project_id ||
        (!id ? ws.projectFilter : null)),
  );
  const panel = (
    <MaterialsPanel
      ws={ws}
      id={id}
      onPreview={setPreview}
      goLibrary={() => {
        setMaterials(false);
        navigate("/workspace/library");
      }}
    />
  );
  const invalidPath =
    !library && !id && !/^\/workspace\/?$/.test(location.pathname);
  if (invalidPath) return <Navigate to="/workspace" replace />;

  return (
    <MotionConfig reducedMotion="user">
      <div className="workspace">
        <a className="ws-skip-link" href="#workspace-main">
          跳到主要内容
        </a>
        <motion.aside
          className="ws-sidebar"
          aria-label="侧栏"
          animate={{ width: collapsed ? 76 : 248 }}
          transition={{
            duration: reduced ? 0 : 0.24,
            ease: [0.23, 1, 0.32, 1],
          }}
        >
          <Sidebar {...sidebarProps} />
        </motion.aside>
        <div className="ws-main-shell">
          <header className="ws-topbar">
            <div className="ws-topbar-leading">
              <button
                className="ws-icon-button ws-mobile-menu"
                aria-label="打开导航"
                onClick={() => setMobileNav(true)}
              >
                <MenuIcon size={20} />
              </button>
              <div className="ws-breadcrumb">
                {group && (
                  <>
                    <span className="ws-breadcrumb-project">
                      <Folder size={15} />
                      {group.name}
                    </span>
                    <ChevronRight size={14} />
                  </>
                )}
                <span title={title}>{title}</span>
              </div>
            </div>
            <div className="ws-topbar-actions">
              {library ? (
                <Link className="ws-text-button" to="/workspace">
                  <ArrowLeft size={16} />
                  <span>回到对话</span>
                </Link>
              ) : (
                <>
                  <ConversationModeSwitch
                    value={ws.conversationMode}
                    onChange={(mode) => void ws.setConversationMode(mode)}
                    disabled={ws.modeChanging || Boolean(id && ws.detail.isPending)}
                    locked={Boolean(id)}
                  />
                  <button
                    className={`ws-materials-trigger ${materials ? "is-active" : ""}`}
                    onClick={() => setMaterials((v) => !v)}
                    aria-label="资料与依据"
                    aria-expanded={materials}
                    aria-controls={
                      materials && !narrow ? "workspace-materials" : undefined
                    }
                  >
                    {materials ? (
                      <PanelRightClose size={18} />
                    ) : (
                      <PanelRightOpen size={18} />
                    )}
                    <span>资料与依据</span>
                    {Boolean(ws.detail.data?.linked_resources.length) && (
                      <span className="ws-count">
                        {ws.detail.data.linked_resources.length}
                      </span>
                    )}
                  </button>
                </>
              )}
            </div>
          </header>
          <div className="ws-work-area">
            <main id="workspace-main" className="ws-main" tabIndex={-1}>
              {library ? (
                <LibraryPage
                  ws={ws}
                  onPreview={setPreview}
                  onDelete={deleteResource}
                />
              ) : (
                <Conversation
                  ws={ws}
                  id={id}
                  openMaterials={() => setMaterials(true)}
                />
              )}
            </main>
            <AnimatePresence initial={false}>
              {materials && !library && !narrow && (
                <motion.aside
                  key="materials"
                  id="workspace-materials"
                  className="ws-desktop-materials"
                  initial={{ width: 0, opacity: 0 }}
                  animate={{ width: 340, opacity: 1 }}
                  exit={{ width: 0, opacity: 0 }}
                  transition={{ duration: 0.24, ease: [0.32, 0.72, 0, 1] }}
                >
                  <div className="ws-desktop-materials-inner">
                    <header>
                      <h2>资料与依据</h2>
                      <IconButton
                        label="关闭资料面板"
                        onClick={() => setMaterials(false)}
                      >
                        <X size={18} />
                      </IconButton>
                    </header>
                    {panel}
                  </div>
                </motion.aside>
              )}
            </AnimatePresence>
          </div>
        </div>
        <Modal
          open={mobileNav}
          onOpenChange={setMobileNav}
          title="工作空间"
          side="left"
        >
          <Sidebar
            {...sidebarProps}
            collapsed={false}
            toggle={() => setMobileNav(false)}
          />
        </Modal>
        <Modal
          open={materials && narrow && !library}
          onOpenChange={setMaterials}
          title="资料与依据"
          description="管理本对话的资料与附件。"
          side="right"
        >
          {panel}
        </Modal>
        <Modal
          open={Boolean(confirmation)}
          onOpenChange={(open) => {
            if (!open && !confirming) setConfirmation(null);
          }}
          title={confirmation?.title || "确认操作"}
          description={confirmation?.description}
        >
          <div className="ws-dialog-actions">
            <button
              className="ws-secondary-button"
              onClick={() => setConfirmation(null)}
              disabled={confirming}
            >
              取消
            </button>
            <button
              className="ws-danger-button"
              disabled={confirming}
              onClick={async () => {
                if (!confirmation || confirming) return;
                setConfirming(true);
                try {
                  if (await ws.act(confirmation.run)) setConfirmation(null);
                } finally { setConfirming(false); }
              }}
            >
              {confirming ? "正在处理…" : confirmation?.action}
            </button>
          </div>
        </Modal>
        <Modal
          open={projectModal}
          onOpenChange={setProjectModal}
          title="新建项目"
          description="将同一事项的相关对话归为一组。"
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!projectName.trim()) return;
              if (
                await ws.act(async () => {
                  const project = await ws.api.createProject(
                    projectName.trim(),
                  );
                  ws.setProjectFilter(project.id);
                })
              )
                setProjectModal(false);
            }}
          >
            <label className="ws-field-label" htmlFor="project-name">
              项目名称
            </label>
            <input
              id="project-name"
              className="ws-field"
              placeholder="例如：某轮货损争议"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              maxLength={80}
              required
            />
            <div className="ws-dialog-actions">
              <button
                type="button"
                className="ws-secondary-button"
                onClick={() => setProjectModal(false)}
              >
                取消
              </button>
              <button
                className="ws-primary-button"
                disabled={ws.busy || !projectName.trim()}
              >
                <Plus size={16} />
                {ws.busy ? "正在创建…" : "创建项目"}
              </button>
            </div>
          </form>
        </Modal>
        <Modal
          open={Boolean(moving)}
          onOpenChange={(open) => {
            if (!open) setMoving(null);
          }}
          title="移动到项目"
          description={`为“${moving?.title || "这段对话"}”选择项目分组。`}
        >
          <label htmlFor="target-project" className="ws-field-label">
            项目
          </label>
          <select
            id="target-project"
            className="ws-field"
            value={targetProject}
            onChange={(e) => setTargetProject(e.target.value)}
          >
            <option value="">不归入项目</option>
            {ws.projects.data?.projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          <div className="ws-dialog-actions">
            <button
              className="ws-secondary-button"
              onClick={() => setMoving(null)}
            >
              取消
            </button>
            <button
              className="ws-primary-button"
              disabled={ws.busy || targetProject === (moving?.project_id || "")}
              onClick={async () => {
                if (
                  moving &&
                  (await ws.act(async () => {
                    if (targetProject)
                      await ws.api.move(moving.id, targetProject);
                    else if (moving.project_id)
                      await ws.api.move(moving.id, moving.project_id, true);
                  }))
                )
                  setMoving(null);
              }}
            >
              {ws.busy ? "正在移动…" : "确认移动"}
            </button>
          </div>
        </Modal>
        <ResourcePreview
          ws={ws}
          resource={preview}
          close={() => setPreview(null)}
        />
        <AnimatePresence>
          {ws.notice && (
            <motion.div
              className="ws-notice"
              role="status"
              initial={{ opacity: 0, transform: "translateY(12px)" }}
              animate={{ opacity: 1, transform: "translateY(0)" }}
              exit={{ opacity: 0, transform: "translateY(8px)" }}
            >
              <p>{ws.notice}</p>
              <IconButton label="关闭提示" onClick={() => ws.setNotice("")}>
                <X size={16} />
              </IconButton>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}

export default function Workspace() {
  const session = useAuthSession();
  return session ? (
    <Desk key={session.account.id} session={session} />
  ) : (
    <Navigate
      to="/login"
      replace
      state={{ notice: getSignedOutNotice() || "请先登录，再进入工作台。" }}
    />
  );
}
