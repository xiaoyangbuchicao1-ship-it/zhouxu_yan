import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  BriefcaseBusiness,
  LoaderCircle,
  MessageCircle,
} from "lucide-react";
import { useAuthSession, saveSession, getSignedOutNotice } from "@/lib/auth-session";
import { revokeSession, type AuthSession } from "@/lib/auth";
import { useWorkspace } from "@/hooks/use-workspace";
import type { Conversation as Chat, ConversationMode, Resource } from "@/lib/workbench-api";
import { createAccountSettingsApi, defaultUserPreferences, type UserPreferences, type UserPromptCommand } from "@/lib/account-settings-api";
import { maritimeApi, type MaritimeSkill } from "@/lib/maritime-api";
import Sidebar, { type SidebarProps } from "@/components/workspace/Sidebar";
import Conversation from "@/components/workspace/Conversation";
import { ResearchPanel } from "@/components/workspace/ResearchPanel";
import ConversationModeSwitch from "@/components/workspace/ConversationModeSwitch";
import {
  LibraryPage,
  MaterialsPanel,
  ResourcePreview,
} from "@/components/workspace/Resources";
import { Modal, IconButton } from "@/components/workspace/Primitives";
import MaritimeDesk, { type OfficePage } from "@/components/workspace/MaritimeDesk";
import { UserCenterDialog, type UserCenterTab } from "@/components/workspace/UserCenterDialog";
import { CommandPalette } from "@/components/workspace/CommandPalette";
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
  const queryClient = useQueryClient();
  const library = location.pathname === "/workspace/library";
  const legacyCases = /^\/workspace\/cases\/?$/.test(location.pathname);
  const officePage = location.pathname.match(/^\/workspace\/(skills|research)\/?$/)?.[1] as OfficePage | undefined;
  const match = location.pathname.match(/^\/workspace\/chat\/([^/]+)\/?$/);
  const id = match?.[1];
  const params = new URLSearchParams(location.search);
  const skillParam = params.get("skill") || undefined;
  const modeParam = params.get("mode");
  const initialMode: ConversationMode | undefined =
    modeParam === "work" ? "work" : modeParam === "chat" ? "chat" : undefined;

  const [userCenterOpen, setUserCenterOpen] = useState(() => params.get("settings") === "1");
  const [userCenterTab, setUserCenterTab] = useState<UserCenterTab>("account");
  const [paletteOpen, setPaletteOpen] = useState(false);

  const settingsApi = useMemo(() => createAccountSettingsApi(session.session.token), [session.session.token]);
  const skillsApi = useMemo(() => maritimeApi(session.session.token), [session.session.token]);

  const preferencesQuery = useQuery({
    queryKey: ["account", session.account.id, "preferences"],
    queryFn: ({ signal }) => settingsApi.getPreferences(signal),
  });
  const commandsQuery = useQuery({
    queryKey: ["account", session.account.id, "commands"],
    queryFn: ({ signal }) => settingsApi.listCommands(signal),
  });
  const summaryQuery = useQuery({
    queryKey: ["account", session.account.id, "summary"],
    queryFn: ({ signal }) => settingsApi.summary(signal),
    enabled: userCenterOpen,
  });
  const skillsQuery = useQuery({
    queryKey: ["maritime", session.account.id, "skills"],
    queryFn: () => skillsApi.skills(),
  });

  const prefs = preferencesQuery.data ?? defaultUserPreferences();
  const commands = commandsQuery.data?.commands ?? [];
  const activeSkill = useMemo<MaritimeSkill | null>(() => {
    if (!skillParam) return null;
    const found = skillsQuery.data?.skills.find((s) => s.id === skillParam);
    if (found) return found;
    return {
      id: skillParam,
      name: skillParam,
      display_name: skillParam,
      mode_scope: initialMode ?? "work",
      summary: "",
      instructions: "",
      version: 1,
      owner_id: null,
      visibility: "builtin",
      status: "ready",
    };
  }, [skillParam, initialMode, skillsQuery.data]);

  const ws = useWorkspace(session, id, {
    skillId: skillParam,
    caseId: params.get("case") || undefined,
    initialMode,
    defaultMode: prefs.default_mode,
    defaultThinkingMode: prefs.default_thinking_mode,
    completionSoundEnabled: prefs.completion_sound_enabled !== false,
  });
  const osReduced = useReducedMotion();
  const reduced = Boolean(osReduced || prefs.reduced_motion);

  useEffect(() => {
    document.documentElement.classList.toggle("ws-reduced-motion", Boolean(prefs.reduced_motion));
    return () => {
      document.documentElement.classList.remove("ws-reduced-motion");
    };
  }, [prefs.reduced_motion]);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [materials, setMaterials] = useState(false);
  const [panelTab, setPanelTab] = useState<"sources" | "materials">("sources");
  const [sourceRunId, setSourceRunId] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  useEffect(() => { setSourceRunId(null); setSourceId(null); }, [id, ws.turn?.runId]);
  function openSources(runId?: string, selectedSource?: string) {
    setSourceRunId(runId ?? null);
    setSourceId(selectedSource ?? null);
    setPanelTab("sources");
    setMaterials(true);
  }
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
  const [modeTransition, setModeTransition] = useState<ConversationMode | null>(null);

  useEffect(() => {
    if (!modeTransition) return;
    const timer = window.setTimeout(
      () => setModeTransition(null),
      reduced ? 0 : 620,
    );
    return () => window.clearTimeout(timer);
  }, [modeTransition, reduced]);

  function changeConversationMode(next: ConversationMode) {
    if (id || ws.busy || ws.modeChanging || ws.conversationMode === next) return;
    setModeTransition(next);
    ws.setConversationMode(next);
  }

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
  function openUserCenter(tab: UserCenterTab = "account") {
    setUserCenterTab(tab);
    setUserCenterOpen(true);
    setMobileNav(false);
  }

  async function startInProject(projectId: string) {
    setMobileNav(false);
    ws.setProjectFilter(projectId);
    await ws.act(async () => {
      const targetMode = prefs.default_mode;
      const created = await ws.api.create(
        "",
        targetMode === "chat" ? undefined : targetMode,
        projectId,
      );
      navigate(`/workspace/chat/${created.id}`);
    });
  }

  function clearMountedSkill() {
    const next = new URLSearchParams(location.search);
    next.delete("skill");
    next.delete("mode");
    const search = next.toString();
    navigate({ pathname: location.pathname, search: search ? `?${search}` : "" }, { replace: true });
  }

  function selectWelcomeSkill(skillId: string, prompt: string, targetMode: ConversationMode) {
    ws.setDraft(prompt);
    if (!id && ws.conversationMode !== targetMode) ws.setConversationMode(targetMode);
    const next = new URLSearchParams(location.search);
    next.set("skill", skillId);
    next.set("mode", targetMode);
    navigate({ pathname: location.pathname, search: `?${next.toString()}` }, { replace: true });
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
    onOpenUserCenter: openUserCenter,
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
  const title = officePage
    ? ({ skills: "技能", research: "海事检索" }[officePage])
    : library
    ? "资料库"
    : ws.detail.data?.conversation.title || "新对话";
  const group = ws.projects.data?.projects.find(
    (p) =>
      p.id ===
      (ws.detail.data?.conversation.project_id ||
        (!id ? ws.projectFilter : null)),
  );
  const panel = (
    <>
    <div className="ws-evidence-tabs" aria-label="侧栏内容">
      <button type="button" aria-pressed={panelTab === "sources"} onClick={() => setPanelTab("sources")}>检索资料</button>
      <button type="button" aria-pressed={panelTab === "materials"} onClick={() => setPanelTab("materials")}>附件与资料库</button>
    </div>
    {panelTab === "sources" ? <ResearchPanel ws={ws} selectedRunId={sourceRunId} onSelect={setSourceRunId} sourceId={sourceId} onSource={setSourceId} /> : (
    <MaterialsPanel
      ws={ws}
      id={id}
      onPreview={setPreview}
      goLibrary={() => {
        setMaterials(false);
        navigate("/workspace/library");
      }}
    />
    )}
    </>
  );
  if (legacyCases) return <Navigate to="/workspace" replace />;
  const invalidPath =
    !library && !officePage && !id && !/^\/workspace\/?$/.test(location.pathname);
  if (invalidPath) return <Navigate to="/workspace" replace />;

  return (
    <MotionConfig reducedMotion={prefs.reduced_motion ? "always" : "user"}>
      <div
        className={`workspace ${modeTransition ? "is-mode-transitioning" : ""} ${prefs.compact_density ? "is-compact" : ""} ${prefs.reduced_motion ? "is-reduced-motion" : ""}`}
      >
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
              {officePage ? (
                <Link className="ws-text-button" to="/workspace">
                  <ArrowLeft size={16} />
                  <span>回到对话</span>
                </Link>
              ) : library ? (
                <Link className="ws-text-button" to="/workspace">
                  <ArrowLeft size={16} />
                  <span>回到对话</span>
                </Link>
              ) : (
                <>
                  <ConversationModeSwitch
                    value={ws.conversationMode}
                    onChange={changeConversationMode}
                    disabled={ws.modeChanging || Boolean(id && ws.detail.isPending)}
                    locked={Boolean(id)}
                    transitioning={Boolean(modeTransition)}
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
            <AnimatePresence initial={false}>
              {modeTransition && (
                <motion.div
                  className="ws-mode-transition"
                  role="status"
                  aria-live="polite"
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                >
                  <LoaderCircle size={13} aria-hidden="true" />
                  正在准备{modeTransition === "work" ? "工作" : "聊天"}空间…
                </motion.div>
              )}
            </AnimatePresence>
          </header>
          <div className="ws-work-area">
            <main id="workspace-main" className="ws-main" tabIndex={-1}>
              {officePage ? (
                <MaritimeDesk
                  key={officePage}
                  page={officePage}
                  token={session.session.token}
                  accountId={session.account.id}
                />
              ) : library ? (
                <LibraryPage
                  ws={ws}
                  onPreview={setPreview}
                  onDelete={deleteResource}
                />
              ) : (
                <Conversation
                  ws={ws}
                  id={id}
                  openMaterials={() => { setPanelTab("materials"); setMaterials(true); }}
                  openSources={openSources}
                  commands={commands}
                  conciseProcess={prefs.concise_process}
                  activeSkill={activeSkill}
                  onClearSkill={clearMountedSkill}
                  onSelectWelcomeSkill={selectWelcomeSkill}
                  onClearProjectFilter={() => ws.setProjectFilter(null)}
                />
              )}
            </main>
            <AnimatePresence initial={false}>
              {materials && !library && !officePage && !narrow && (
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
          open={materials && narrow && !library && !officePage}
          onOpenChange={setMaterials}
          title="资料与依据"
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
        <UserCenterDialog
          open={userCenterOpen}
          onOpenChange={setUserCenterOpen}
          initialTab={userCenterTab}
          account={session.account}
          preferences={prefs}
          preferencesUnavailable={!preferencesQuery.data}
          commands={commands}
          summary={summaryQuery.data}
          summaryLoading={summaryQuery.isFetching}
          onUpdateDisplayName={async (displayName) => {
            const updated = await settingsApi.updateProfile(displayName);
            saveSession({ ...session, account: updated });
            await queryClient.invalidateQueries({ queryKey: ["account", session.account.id, "summary"] });
            return updated;
          }}
          onChangePassword={async (cur, next) => {
            await settingsApi.changePassword(cur, next);
          }}
          onSavePreferences={async (patch: Partial<UserPreferences>) => {
            const saved = await settingsApi.updatePreferences(patch);
            queryClient.setQueryData(["account", session.account.id, "preferences"], saved);
            return saved;
          }}
          onCreateCommand={async (input) => {
            const created = await settingsApi.createCommand(input);
            queryClient.setQueryData<{ commands: UserPromptCommand[] }>(
              ["account", session.account.id, "commands"],
              (prev) => ({ commands: [...(prev?.commands ?? []), created] }),
            );
            return created;
          }}
          onUpdateCommand={async (commandId, patch) => {
            const updated = await settingsApi.updateCommand(commandId, patch);
            queryClient.setQueryData<{ commands: UserPromptCommand[] }>(
              ["account", session.account.id, "commands"],
              (prev) => ({
                commands: (prev?.commands ?? []).map((c) => (c.id === commandId ? updated : c)),
              }),
            );
            return updated;
          }}
          onDeleteCommand={async (commandId) => {
            await settingsApi.deleteCommand(commandId);
            queryClient.setQueryData<{ commands: UserPromptCommand[] }>(
              ["account", session.account.id, "commands"],
              (prev) => ({
                commands: (prev?.commands ?? []).filter((c) => c.id !== commandId),
              }),
            );
          }}
          onOpenCommandPalette={() => setPaletteOpen(true)}
          onSignOut={() => sidebarProps.logout()}
        />
        <CommandPalette
          open={paletteOpen}
          onOpenChange={setPaletteOpen}
          conversations={ws.history.data?.conversations ?? []}
          projects={ws.projects.data?.projects ?? []}
          commands={commands}
          canPause={ws.turn?.status === "running"}
          canContinue={Boolean(
            ws.turn && (ws.turn.status === "paused" || (ws.turn.status === "failed" && ws.turn.recovery.can_continue)),
          )}
          onNewChat={() => {
            navigate("/workspace");
          }}
          onSelectConversation={(conversationId) => navigate(`/workspace/chat/${conversationId}`)}
          onStartInProject={(projectId) => { void startInProject(projectId); }}
          onApplyCommand={(cmd) => {
            ws.setDraft(cmd.content);
            if (!id && cmd.mode_hint && ws.conversationMode !== cmd.mode_hint) {
              ws.setConversationMode(cmd.mode_hint);
            }
            if (officePage || library) navigate("/workspace");
          }}
          onNavigateOffice={(path) => navigate(path)}
          onOpenUserCenter={openUserCenter}
          onPauseRun={() => void ws.pause()}
          onContinueRun={() => void ws.continueRun()}
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
        <AnimatePresence>
          {modeTransition && (
            <motion.div
              className={`ws-mode-refresh is-${modeTransition}`}
              role="status"
              aria-live="polite"
              data-testid="mode-refresh"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduced ? 0 : 0.16, ease: "easeOut" }}
            >
              <div className="ws-mode-refresh-sheen" aria-hidden="true" />
              <motion.div
                className="ws-mode-refresh-card"
                initial={{ opacity: 0, y: 10, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.98 }}
                transition={{
                  duration: reduced ? 0 : 0.36,
                  delay: reduced ? 0 : 0.06,
                  ease: [0.16, 1, 0.3, 1],
                }}
              >
                <span className="ws-mode-refresh-icon" aria-hidden="true">
                  {modeTransition === "work" ? <BriefcaseBusiness size={18} /> : <MessageCircle size={18} />}
                </span>
                <div className="ws-mode-refresh-copy">
                  <strong>正在切换到{modeTransition === "work" ? "工作" : "聊天"}模式</strong>
                  <span>为这段新对话准备独立空间</span>
                </div>
                <span className="ws-mode-refresh-meter" aria-hidden="true"><span /></span>
              </motion.div>
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
