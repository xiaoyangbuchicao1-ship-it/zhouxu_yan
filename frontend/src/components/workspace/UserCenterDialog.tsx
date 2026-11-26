import { useEffect, useState, type FormEvent } from "react";
import {
  Check, KeyRound, LogOut, Plus, ShieldCheck, Slash,
  Sparkles, SlidersHorizontal, Trash2, User, type LucideIcon,
} from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { KeyCaps } from "./KeyCaps";
import type { AuthSession } from "@/lib/auth";
import type {
  AccountSummary,
  ThinkingModeValue,
  UserPreferences,
  UserPromptCommand,
} from "@/lib/account-settings-api";

export type UserCenterTab = "account" | "memory" | "commands" | "habits";

export interface UserCenterDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialTab?: UserCenterTab;
  account: AuthSession["account"];
  preferences: UserPreferences;
  commands: UserPromptCommand[];
  summary?: AccountSummary | null;
  summaryLoading?: boolean;
  onUpdateDisplayName: (displayName: string) => Promise<AuthSession["account"]>;
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  onSavePreferences: (patch: Partial<UserPreferences>) => Promise<UserPreferences>;
  onCreateCommand: (input: {
    trigger: string;
    title: string;
    content: string;
    mode_hint?: "chat" | "work" | null;
  }) => Promise<UserPromptCommand>;
  onUpdateCommand: (
    id: string,
    patch: Partial<{ trigger: string; title: string; content: string; mode_hint: "chat" | "work" | null }>,
  ) => Promise<UserPromptCommand>;
  onDeleteCommand: (id: string) => Promise<void>;
  onOpenCommandPalette?: () => void;
  onSignOut?: () => void;
  /** 已保存的偏好没读到时为 true；此时不能保存记忆，以免覆盖。 */
  preferencesUnavailable?: boolean;
}

const THINKING_OPTIONS: { value: ThinkingModeValue; label: string }[] = [
  { value: "none", label: "关闭" },
  { value: "low", label: "轻度" },
  { value: "medium", label: "标准" },
  { value: "high", label: "深度" },
];

const NAV_ITEMS: { value: UserCenterTab; label: string; Icon: LucideIcon }[] = [
  { value: "account",  label: "账号",      Icon: User },
  { value: "memory",   label: "记忆",      Icon: Sparkles },
  { value: "commands", label: "快捷指令",  Icon: Slash },
  { value: "habits",   label: "偏好与快捷键", Icon: SlidersHorizontal },
];

export function UserCenterDialog({
  open,
  onOpenChange,
  initialTab = "account",
  account,
  preferences,
  commands,
  summary,
  summaryLoading = false,
  onUpdateDisplayName,
  onChangePassword,
  onSavePreferences,
  onCreateCommand,
  onUpdateCommand,
  onDeleteCommand,
  onOpenCommandPalette,
  onSignOut,
  preferencesUnavailable = false,
}: UserCenterDialogProps) {
  const [tab, setTab] = useState<UserCenterTab>(initialTab);
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");

  const [displayName, setDisplayName] = useState(account.display_name || "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);

  const [memoryEnabled, setMemoryEnabled] = useState(preferences.memory_enabled);
  const [memoryText, setMemoryText] = useState(preferences.memory_text);
  const [savingMemory, setSavingMemory] = useState(false);

  const [editingCmdId, setEditingCmdId] = useState<string | null>(null);
  const [cmdTrigger, setCmdTrigger] = useState("");
  const [cmdTitle, setCmdTitle] = useState("");
  const [cmdModeHint, setCmdModeHint] = useState<"" | "chat" | "work">("");
  const [cmdContent, setCmdContent] = useState("");
  const [savingCmd, setSavingCmd] = useState(false);

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setStatusMessage("");
      setErrorMessage("");
      setMemoryEnabled(preferences.memory_enabled);
      setMemoryText(preferences.memory_text);
    }
  }, [open, initialTab]);

  useEffect(() => {
    setDisplayName(account.display_name || "");
  }, [account.display_name]);

  function flashStatus(message: string) {
    setErrorMessage("");
    setStatusMessage(message);
  }

  function flashError(error: unknown) {
    setStatusMessage("");
    setErrorMessage(error instanceof Error ? error.message : "保存未完成，请稍后重试。");
  }

  async function submitDisplayName(event: FormEvent) {
    event.preventDefault();
    const cleaned = displayName.trim();
    if (!cleaned) { flashError(new Error("请输入显示称呼。")); return; }
    setSavingProfile(true);
    try {
      await onUpdateDisplayName(cleaned);
      flashStatus("称呼已更新。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingProfile(false);
    }
  }

  async function submitPasswordChange(event: FormEvent) {
    event.preventDefault();
    if (!currentPassword) { flashError(new Error("请输入当前密码。")); return; }
    if ([...newPassword].length < 12) { flashError(new Error("新密码至少需要 12 位字符。")); return; }
    if (newPassword !== confirmPassword) { flashError(new Error("两次输入的新密码不一致。")); return; }
    setSavingPassword(true);
    try {
      await onChangePassword(currentPassword, newPassword);
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      flashStatus("密码已更新。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingPassword(false);
    }
  }

  async function saveMemory(nextEnabled = memoryEnabled, nextText = memoryText) {
    setSavingMemory(true);
    try {
      await onSavePreferences({ memory_enabled: nextEnabled, memory_text: nextText });
      flashStatus("长期记忆设置已保存。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingMemory(false);
    }
  }

  function startEditCommand(cmd: UserPromptCommand) {
    setEditingCmdId(cmd.id);
    setCmdTrigger(cmd.trigger);
    setCmdTitle(cmd.title);
    setCmdModeHint(cmd.mode_hint || "");
    setCmdContent(cmd.content);
    setErrorMessage("");
  }

  function resetCommandForm() {
    setEditingCmdId(null);
    setCmdTrigger(""); setCmdTitle(""); setCmdModeHint(""); setCmdContent("");
  }

  async function submitCommand(event: FormEvent) {
    event.preventDefault();
    const cleanTrigger = cmdTrigger.trim().replace(/^\/+/, "").toLowerCase();
    const cleanTitle = cmdTitle.trim();
    const cleanContent = cmdContent.trim();
    if (!cleanTrigger || !cleanTitle || !cleanContent) {
      flashError(new Error("请填写指令代号、名称与正文。")); return;
    }
    setSavingCmd(true);
    try {
      const payload = { trigger: cleanTrigger, title: cleanTitle, content: cleanContent, mode_hint: cmdModeHint || null };
      if (editingCmdId) {
        await onUpdateCommand(editingCmdId, payload);
        flashStatus(`指令 /${cleanTrigger} 已更新。`);
      } else {
        await onCreateCommand(payload);
        flashStatus(`已添加指令 /${cleanTrigger}。`);
      }
      resetCommandForm();
    } catch (error) {
      flashError(error);
    } finally {
      setSavingCmd(false);
    }
  }

  async function removeCommand(cmd: UserPromptCommand) {
    try {
      await onDeleteCommand(cmd.id);
      if (editingCmdId === cmd.id) resetCommandForm();
      flashStatus(`已移除 /${cmd.trigger}。`);
    } catch (error) {
      flashError(error);
    }
  }

  async function patchHabit(patch: Partial<UserPreferences>, label: string) {
    try {
      await onSavePreferences(patch);
      flashStatus(`${label}已同步。`);
    } catch (error) {
      flashError(error);
    }
  }

  const initials = (account.display_name || account.email || "U").slice(0, 1).toUpperCase();
  const caps = summary?.capabilities;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="ws-dialog ws-user-center-dialog border-[#1e293b] bg-[#0d1218] text-[#eef2f6] p-0 gap-0 overflow-hidden flex flex-col"
        data-testid="user-center-dialog"
      >
        {/* Header */}
        <div className="ws-uc-header">
          <span className="ws-uc-avatar" aria-hidden="true">{initials}</span>
          <div className="min-w-0 flex-1">
            <DialogTitle className="text-[15px] font-medium text-[#f4f7fb] flex items-center gap-2 leading-none">
              <span className="truncate">{account.display_name || "个人账户"}</span>
              <span className="ws-uc-role-pill">
                {account.role === "admin" ? "管理员" : "个人空间"}
              </span>
            </DialogTitle>
            <DialogDescription className="text-[12px] text-[#7c8b9c] mt-1.5 leading-none">
              {account.email}
            </DialogDescription>
          </div>
        </div>

        {/* Body: left nav + right content */}
        <div className="ws-uc-body">
          <nav className="ws-uc-nav" aria-label="用户中心导航">
            {NAV_ITEMS.map(({ value, label, Icon }) => (
              <button
                key={value}
                type="button"
                className={`ws-uc-nav-btn ${tab === value ? "is-active" : ""}`}
                onClick={() => { setTab(value); setStatusMessage(""); setErrorMessage(""); }}
                aria-current={tab === value ? "page" : undefined}
              >
                <Icon size={15} aria-hidden />
                {label}
              </button>
            ))}
          </nav>

          <div className="ws-uc-content">
            {/* Account tab */}
            {tab === "account" && (
              <div className="ws-uc-rows">
                {/* Display name */}
                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>显示称呼</h3>
                    <p>显示在侧栏的名字。</p>
                  </div>
                  <form onSubmit={submitDisplayName} className="ws-uc-row-ctrl">
                    <input
                      id="uc-display-name"
                      aria-label="显示称呼"
                      className="ws-uc-input"
                      value={displayName}
                      maxLength={80}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder="输入称呼"
                    />
                    <button type="submit" className="ws-uc-secondary-btn" disabled={savingProfile}>
                      {savingProfile ? "保存中…" : "保存"}
                    </button>
                  </form>
                </div>
                <hr className="ws-uc-divider" />

                {/* Password */}
                <form className="ws-uc-section" onSubmit={submitPasswordChange}>
                  <div className="ws-uc-section-head">
                    <h3><KeyRound size={14} aria-hidden="true" className="text-[#89aacc]" />修改密码</h3>
                    <p>新密码至少 12 位。</p>
                  </div>
                  <div className="ws-uc-pwd-grid">
                    <input type="password" aria-label="当前密码" className="ws-uc-input" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} placeholder="当前密码" autoComplete="current-password" />
                    <input type="password" aria-label="新密码" className="ws-uc-input" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="新密码" autoComplete="new-password" />
                    <input type="password" aria-label="确认新密码" className="ws-uc-input" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="确认新密码" autoComplete="new-password" />
                  </div>
                  <div className="mt-3 flex justify-end">
                    <button type="submit" className="ws-uc-secondary-btn" disabled={savingPassword}>
                      {savingPassword ? "提交中…" : "更新密码"}
                    </button>
                  </div>
                </form>
                <hr className="ws-uc-divider" />

                {/* Capabilities */}
                <div className="ws-uc-section">
                  <div className="ws-uc-section-head">
                    <h3><ShieldCheck size={14} aria-hidden="true" className="text-[#89aacc]" />我的权限</h3>
                  </div>
                  {summaryLoading && !caps ? (
                    <p className="text-xs text-[#8f9eb0]">读取中…</p>
                  ) : (
                    <dl className="ws-uc-kv">
                      <div>
                        <dt>可用模型</dt>
                        <dd>
                          {caps?.model_policy === "disabled"
                            ? "已停用"
                            : caps?.model_policy === "allowlist"
                              ? caps.allowed_model_labels.join("、") || "暂未分配"
                              : "全部已启用模型"}
                        </dd>
                      </div>
                      <div>
                        <dt>技能权限</dt>
                        <dd>
                          {caps?.skill_policy === "disabled"
                            ? "已停用"
                            : caps?.can_upload_skill
                              ? "内置技能，可上传自己的技能"
                              : "内置技能"}
                        </dd>
                      </div>
                    </dl>
                  )}
                </div>

                {/* Sign out */}
                {onSignOut && (
                  <>
                    <hr className="ws-uc-divider" />
                    <div className="ws-uc-signout-row">
                      <button type="button" className="ws-uc-danger-btn" onClick={onSignOut}>
                        <LogOut size={14} aria-hidden="true" />
                        退出登录
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Memory tab */}
            {tab === "memory" && (
              <div className="ws-uc-rows">
                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>长期记忆</h3>
                    <p>开启后，每次提问都会带上下面这段话。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <Switch
                      id="uc-memory-enabled"
                      aria-label="启用长期记忆"
                      checked={memoryEnabled}
                      disabled={preferencesUnavailable}
                      onCheckedChange={(checked) => {
                        setMemoryEnabled(checked);
                        void onSavePreferences({ memory_enabled: checked })
                          .then(() => flashStatus(checked ? "长期记忆已启用。" : "长期记忆已关闭。"))
                          .catch(flashError);
                      }}
                    />
                  </div>
                </div>
                <hr className="ws-uc-divider" />
                <div className="ws-uc-section">
                  <textarea
                    id="uc-memory-text"
                    aria-label="个人记忆正文"
                    className="ws-uc-textarea"
                    rows={5}
                    maxLength={1200}
                    value={memoryText}
                    onChange={(e) => setMemoryText(e.target.value)}
                    placeholder="例如：我是海商法研究与航运合规从业者，回答优先写适用条款与裁判要点，避免泛泛背景介绍。"
                  />
                  {preferencesUnavailable && <p className="ws-uc-status is-error" role="alert">没能读到你已保存的记忆，现在保存会覆盖它。请关闭后重新打开设置。</p>}
                  <div className="mt-2 flex items-center justify-between gap-3">
                    <span className="text-xs text-[#7c8b9c] tabular-nums">{memoryText.length} / 1200 字</span>
                    <button type="button" className="ws-uc-primary-btn" disabled={savingMemory || preferencesUnavailable} onClick={() => void saveMemory(memoryEnabled, memoryText)}>
                      {savingMemory ? "保存中…" : "保存"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Commands tab */}
            {tab === "commands" && (
              <div>
                <div className="ws-uc-cmd-list">
                  {commands.map((cmd) => (
                    <div key={cmd.id} className="ws-uc-cmd-row">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <code className="ws-uc-code">/{cmd.trigger}</code>
                          <span className="text-sm font-medium text-[#eef2f6] truncate">{cmd.title}</span>
                          {cmd.mode_hint && (
                            <span className="ws-uc-tag">{cmd.mode_hint === "work" ? "工作模式" : "聊天模式"}</span>
                          )}
                        </div>
                        <p className="text-xs text-[#8f9eb0] truncate mt-0.5">{cmd.content}</p>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button type="button" className="ws-uc-ghost-btn" onClick={() => startEditCommand(cmd)}>编辑</button>
                        <button type="button" className="ws-uc-icon-danger" aria-label={`删除指令 ${cmd.trigger}`} onClick={() => void removeCommand(cmd)}>
                          <Trash2 size={14} aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  ))}
                  {!commands.length && (
                    <p className="text-xs text-[#7c8b9c] py-2">还没有快捷指令，可在下方新建。</p>
                  )}
                </div>

                <form className="ws-uc-cmd-form" onSubmit={submitCommand}>
                  <p className="text-xs text-[#8b9bad] mb-3">
                    {editingCmdId ? "编辑指令" : "新建快捷指令"}
                  </p>
                  <div className="ws-uc-cmd-top-row">
                    <input aria-label="指令代号" className="ws-uc-input" maxLength={32} value={cmdTrigger} onChange={(e) => setCmdTrigger(e.target.value)} placeholder="代号，如：brief" />
                    <input aria-label="指令名称" className="ws-uc-input" maxLength={80} value={cmdTitle} onChange={(e) => setCmdTitle(e.target.value)} placeholder="名称，如：案件摘要" />
                    <select aria-label="建议模式" className="ws-uc-input" value={cmdModeHint} onChange={(e) => setCmdModeHint(e.target.value as "" | "chat" | "work")}>
                      <option value="">不切换模式</option>
                      <option value="work">建议使用工作模式</option>
                      <option value="chat">建议使用聊天模式</option>
                    </select>
                  </div>
                  <textarea aria-label="指令模板正文" className="ws-uc-textarea mt-2" rows={3} maxLength={4000} value={cmdContent} onChange={(e) => setCmdContent(e.target.value)} placeholder="填写展开后的提问正文…" />
                  <div className="flex items-center justify-end gap-2 mt-2">
                    {editingCmdId && (
                      <button type="button" className="ws-uc-ghost-btn" onClick={resetCommandForm}>取消编辑</button>
                    )}
                    <button type="submit" className="ws-uc-primary-btn" disabled={savingCmd}>
                      <Plus size={14} aria-hidden="true" />
                      {editingCmdId ? "保存指令修改" : "添加指令"}
                    </button>
                  </div>
                </form>
              </div>
            )}

            {/* Habits tab */}
            {tab === "habits" && (
              <div className="ws-uc-rows">
                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>新建对话默认模式</h3>
                    <p>每次点「新建对话」时预设进入的模式。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <div className="ws-uc-segment" role="group" aria-label="新建对话默认模式">
                      {(["chat", "work"] as const).map((mode) => (
                        <button key={mode} type="button" className={preferences.default_mode === mode ? "is-active" : ""} onClick={() => void patchHabit({ default_mode: mode }, "默认对话模式")}>
                          {mode === "chat" ? "聊天" : "工作"}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <hr className="ws-uc-divider" />

                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>默认思考深度</h3>
                    <p>所选模型支持该深度时自动启用。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <div className="ws-uc-segment" role="group" aria-label="默认思考深度">
                      {THINKING_OPTIONS.map((opt) => (
                        <button key={opt.value} type="button" className={preferences.default_thinking_mode === opt.value ? "is-active" : ""} onClick={() => void patchHabit({ default_thinking_mode: opt.value }, "默认思考深度")}>
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <hr className="ws-uc-divider" />

                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>简洁运行过程</h3>
                    <p>把检索步骤收成一行进度。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <Switch id="uc-concise-process" aria-label="简洁运行过程" checked={preferences.concise_process} onCheckedChange={(checked) => void patchHabit({ concise_process: checked }, "简洁运行过程")} />
                  </div>
                </div>
                <hr className="ws-uc-divider" />

                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>紧凑信息密度</h3>
                    <p>收紧间距，一屏显示更多内容。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <Switch id="uc-compact-density" aria-label="紧凑信息密度" checked={preferences.compact_density} onCheckedChange={(checked) => void patchHabit({ compact_density: checked }, "紧凑信息密度")} />
                  </div>
                </div>
                <hr className="ws-uc-divider" />

                <div className="ws-uc-row">
                  <div className="ws-uc-row-label">
                    <h3>减弱界面动效</h3>
                    <p>关闭切换过渡与面板滑入动画。</p>
                  </div>
                  <div className="ws-uc-row-ctrl">
                    <Switch id="uc-reduced-motion" aria-label="减弱界面动效" checked={preferences.reduced_motion} onCheckedChange={(checked) => void patchHabit({ reduced_motion: checked }, "减弱界面动效")} />
                  </div>
                </div>
                <hr className="ws-uc-divider" />

                <div className="ws-uc-section">
                  <div className="ws-uc-section-head">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <h3>快捷键</h3>
                      </div>
                      {onOpenCommandPalette && (
                        <button type="button" className="ws-uc-secondary-btn" onClick={() => { onOpenChange(false); onOpenCommandPalette(); }}>
                          打开命令面板
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="ws-uc-shortcuts">
                    <div>
                      <span>命令面板</span>
                      <KeyCaps keys={["mod", "K"]} />
                    </div>
                    <div>
                      <span>快捷指令</span>
                      <KeyCaps keys={["/"]} />
                    </div>
                    <div>
                      <span>发送</span>
                      <KeyCaps keys={["enter"]} />
                    </div>
                    <div>
                      <span>换行</span>
                      <KeyCaps keys={["shift", "enter"]} />
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer status bar */}
        <div className="ws-uc-footer" aria-live="polite" role="status">
          {errorMessage ? (
            <span className="ws-uc-status is-error">{errorMessage}</span>
          ) : statusMessage ? (
            <span className="ws-uc-status is-ok">
              <Check size={12} aria-hidden="true" />
              {statusMessage}
            </span>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
