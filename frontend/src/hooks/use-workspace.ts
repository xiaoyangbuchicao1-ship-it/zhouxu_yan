import { readThinkingMode, type ThinkingMode } from "@/lib/thinking-modes";
import { conversationTitle, isUntitledConversation } from "@/lib/conversation-title";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { saveSession } from "@/lib/auth-session";
import type { AuthSession } from "@/lib/auth";
import { workbenchApi, WorkbenchError, acceptStreamSequence, type Activity, type Artifact, type ConversationMode, type Material, type RunEnvelope, type RunRecovery, type RunStatus, type Conversation, type ConversationDetail, type SourceReference, type TaskContextFacts } from "@/lib/workbench-api";

export interface PendingTurn { conversationId: string; runId?: string; mode: ConversationMode; question: string; answer: string; materials: Material[]; updates: string[]; activities: Activity[]; sources: SourceReference[]; artifacts: Artifact[]; recovery: RunRecovery; state: "receiving" | "waiting" | "completed" | "failed" | "stopped" | "interrupted"; status: RunStatus; revision: number; createdAt?: string; error?: string; inputRequest?: { requestId: string; question: string }; }
export interface Upload { key: string; conversationId: string; file: File; error?: string; }
const terminal = new Set<RunStatus>(["completed", "failed", "stopped", "interrupted"]);
const stateFor = (status: RunStatus): PendingTurn["state"] => status === "waiting_user" ? "waiting" : terminal.has(status) ? status as PendingTurn["state"] : "receiving";

export interface WorkspaceTaskContext {
  skillId?: string;
  caseId?: string;
}

export function useWorkspace(session: AuthSession, id?: string, taskContext: WorkspaceTaskContext = {}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [lifetime, setLifetime] = useState(() => new AbortController());
  const api = useMemo(() => workbenchApi(session.session.token, lifetime.signal), [session.session.token, lifetime]);
  const scope = useMemo(() => ["workbench", session.account.id], [session.account.id]);
  const attachmentKey = `zhouxu.attachments.v1.${session.account.id}`;
  const thinkingKey = `zhouxu.thinking.v1.${session.account.id}`;
  const suppressedRunsKey = `zhouxu.suppressed-runs.v1.${session.account.id}`;
  const [thinkingMode, updateThinkingMode] = useState<ThinkingMode>(() => readThinkingMode(thinkingKey));
  const [draftConversationMode, setDraftConversationMode] = useState<ConversationMode>("chat");
  const setThinkingMode = (mode: ThinkingMode) => {
    updateThinkingMode(mode);
    try { sessionStorage.setItem(thinkingKey, mode); } catch { /* Keep the in-memory choice. */ }
  };
  const [notice, setNotice] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<Record<string, Material[]>>(() => {
    try { return JSON.parse(sessionStorage.getItem(attachmentKey) || "{}"); } catch { return {}; }
  });
  const [cards, setCards] = useState<Record<string, PendingTurn>>({});
  const [suppressedRunIds, setSuppressedRunIds] = useState<string[]>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(suppressedRunsKey) || "[]");
      return Array.isArray(value) ? value.filter((runId): runId is string => typeof runId === "string") : [];
    } catch { return []; }
  });
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [projectFilter, setProjectFilter] = useState<string | null>(null);
  const taskFacts = useMemo<TaskContextFacts>(() => ({
    ...(taskContext.skillId ? { skill_id: taskContext.skillId } : {}),
    ...(taskContext.caseId ? { case_id: taskContext.caseId } : {}),
  }), [taskContext.caseId, taskContext.skillId]);
  const controllers = useRef<Record<string, AbortController>>({});
  const sequences = useRef<Record<string, number>>({});
  const submitLock = useRef(false);
  const creation = useRef<Promise<string>>();
  const naming = useRef(new Set<string>());
  const projects = useQuery({ queryKey: [...scope, "projects"], queryFn: () => api.projects(), retry: false });
  const history = useQuery({ queryKey: [...scope, "history"], queryFn: ({ signal }) => api.conversations(signal), retry: false });
  const resources = useQuery({ queryKey: [...scope, "resources"], queryFn: ({ signal }) => api.resources(signal), retry: false });
  const detail = useQuery({ queryKey: [...scope, "chat", id], queryFn: ({ signal }) => api.detail(id!, signal), enabled: Boolean(id), retry: false });
  const conversationMode: ConversationMode = detail.data?.conversation.mode || (id ? "chat" : draftConversationMode);
  const currentCards = Object.values(cards).filter(c => c.conversationId === id);
  const turn = currentCards.find(c => c.status === "running") || currentCards.find(c => c.status === "queued") || currentCards.find(c => c.status === "waiting_user") || currentCards.at(-1) || null;
  const queuedRuns = currentCards
    .filter(c => c.status === "queued")
    .sort((left, right) => (left.createdAt || "").localeCompare(right.createdAt || ""));
  const draft = drafts[id || "new"] || "";
  const draftMaterials = attachments[id || "new"] || [];
  const setDraft = (value: string) => setDrafts(current => ({ ...current, [id || "new"]: value }));
  useEffect(() => {
    if (detail.data?.conversation.mode) setDraftConversationMode(detail.data.conversation.mode);
    if (!id) setDraftConversationMode("chat");
  }, [detail.data?.conversation.mode, id]);
  useEffect(() => { try { sessionStorage.setItem(attachmentKey, JSON.stringify(attachments)); } catch { /* memory state remains usable */ } }, [attachmentKey, attachments]);
  useEffect(() => {
    if (lifetime.signal.aborted) setLifetime(new AbortController());
    return () => { lifetime.abort(); Object.values(controllers.current).forEach(c => c.abort()); client.removeQueries({ queryKey: scope }); };
  }, [client, lifetime, scope]);
  const report = useCallback((error: unknown) => {
    if (lifetime.signal.aborted) return;
    if (error instanceof WorkbenchError && error.status === 401) {
      saveSession(null, "登录状态已失效，请重新登录。"); navigate("/login", { replace: true }); return;
    }
    setNotice(error instanceof Error ? error.message : "操作未完成，请稍后重试。");
  }, [lifetime, navigate]);
  const refresh = useCallback(async () => { await client.invalidateQueries({ queryKey: scope }); }, [client, scope]);
  async function act(operation: () => Promise<unknown>) { try { await operation(); await refresh(); return true; } catch (e) { report(e); return false; } }
  const nameConversation = useCallback(async (conversationId: string, question: string) => {
    const cached = client.getQueryData<ConversationDetail>([...scope, "chat", conversationId]);
    const listed = client.getQueryData<{ conversations: Conversation[] }>([...scope, "history"]);
    const currentTitle = cached?.conversation.title ?? listed?.conversations.find(chat => chat.id === conversationId)?.title;
    if (!isUntitledConversation(currentTitle) || naming.current.has(conversationId) || !question.trim()) return;
    naming.current.add(conversationId);
    try {
      const chat = await api.update(conversationId, { title: conversationTitle(question) });
      client.setQueryData<{ conversations: Conversation[] }>([...scope, "history"], current => current ? { ...current, conversations: current.conversations.map(item => item.id === chat.id ? { ...item, ...chat } : item) } : current);
      client.setQueryData<ConversationDetail>([...scope, "chat", conversationId], current => current ? { ...current, conversation: { ...current.conversation, ...chat } } : current);
    } finally { naming.current.delete(conversationId); }
  }, [api, client, scope]);
  useEffect(() => {
    const firstQuestion = detail.data?.messages.find(message => message.role === "user" && message.content.trim())?.content;
    if (id && firstQuestion && isUntitledConversation(detail.data?.conversation.title)) {
      void nameConversation(id, firstQuestion).catch(report);
    }
  }, [detail.data, id, nameConversation, report]);
  async function ensureConversation(title = "") {
    if (id) return id;
    if (!creation.current) creation.current = (draftConversationMode === "chat" ? api.create(title) : api.create(title, draftConversationMode)).then(chat => {
      if (lifetime.signal.aborted) throw new DOMException("Aborted", "AbortError");
      setDrafts(current => { const next = { ...current, [chat.id]: current.new || current[chat.id] || "" }; delete next.new; return next; });
      const query = new URLSearchParams();
      if (taskContext.skillId) query.set("skill", taskContext.skillId);
      if (taskContext.caseId) query.set("case", taskContext.caseId);
      navigate(`/workspace/chat/${chat.id}${query.toString() ? `?${query}` : ""}`, { replace: true });
      void client.invalidateQueries({ queryKey: [...scope, "history"] }); return chat.id;
    }).finally(() => { creation.current = undefined; });
    return creation.current;
  }
  const setConversationMode = useCallback((mode: ConversationMode) => {
    if (!id) {
      setDraftConversationMode(mode);
      setNotice(mode === "work"
        ? "已选择工作模式。发送第一条消息后，这个会话将固定为工作会话。"
        : "已选择聊天模式。发送第一条消息后，这个会话将固定为聊天会话。"
      );
      return;
    }
    setNotice("会话模式创建后固定；如需另一种模式，请新建会话。");
  }, [id]);
  const update = useCallback((runId: string, fn: (card: PendingTurn) => PendingTurn) => setCards(current => current[runId] ? { ...current, [runId]: fn(current[runId]) } : current), []);
  const applyRun = useCallback(async (conversationId: string, run: RunEnvelope, materials?: Material[]) => {
    const recovery: RunRecovery = run.recovery || { mode: "none", can_continue: false, can_retry: false, message: null };
    setCards(current => {
      const previous = current[run.run_id];
      if (previous && previous.revision > run.revision) return current;
      return { ...current, [run.run_id]: {
        conversationId, runId: run.run_id, mode: run.payload.mode || "chat", question: run.payload.text, revision: run.revision,
        answer: (run.last_sequence || 0) >= (sequences.current[run.run_id] || 0) ? (run.output ?? run.partial_output ?? previous?.answer ?? "") : (previous?.answer || ""),
        materials: [...new Map([...(previous?.materials || []), ...(materials || [])].map(m => [m.id, m])).values()], updates: Object.values({ ...((run.payload.facts.user_answers || {}) as Record<string, unknown>), ...run.answers }).filter((v): v is string => typeof v === "string"), activities: previous?.activities || [],
        sources: run.sources?.length ? run.sources : previous?.sources || [],
        artifacts: run.artifacts?.length ? run.artifacts : previous?.artifacts || [],
        recovery,
        status: run.status, state: stateFor(run.status), error: recovery.message || undefined,
        createdAt: run.created_at || previous?.createdAt,
        inputRequest: run.status === "waiting_user" ? previous?.inputRequest : undefined,
      } };
    });
    if (run.status === "waiting_user" && run.pending_request_ids[0]) {
      const value = await api.inputRequest(run.run_id, conversationId, run.pending_request_ids[0]);
      update(run.run_id, card => card.status === "waiting_user" && card.revision === run.revision ? { ...card, inputRequest: { requestId: value.request.request_id, question: value.request.question } } : card);
    }
  }, [api, update]);
  const follow = useCallback(async (conversationId: string, runId: string, controller: AbortController) => {
    let failures = 0;
    try {
      while (!controller.signal.aborted) {
        try {
          await api.stream(conversationId, runId, event => {
            if (!acceptStreamSequence(sequences.current[runId] || 0, event)) return;
            if (event.type !== "lifecycle") sequences.current[runId] = event.sequence;
            if (event.type === "delta") update(runId, c => ({ ...c, answer: c.answer + event.text, status: "running", state: "receiving" }));
            if (event.type === "answer" || event.type === "done") update(runId, c => ({ ...c, answer: event.text }));
            if (event.type === "progress") update(runId, c => ({ ...c, activities: [...c.activities.filter(a => a.id !== event.activity.id).map(a => ({ ...a, status: "completed" })), event.activity] }));
            if (event.type === "error") update(runId, c => ({ ...c, error: event.message }));
            if (event.type === "lifecycle") void applyRun(conversationId, event.run).catch(report);
          }, controller.signal, sequences.current[runId] || 0);
        const { snapshot } = await api.snapshot(conversationId, controller.signal);
        const run = snapshot.runs.find(r => r.run_id === runId);
        if (!run) return;
        await applyRun(conversationId, run);
        if (terminal.has(run.status) || run.status === "waiting_user") {
          await client.invalidateQueries({ queryKey: [...scope, "chat", conversationId] });
          await client.invalidateQueries({ queryKey: [...scope, "history"] });
          // The presentation buffer acknowledges completion after its final characters appear.
          return;
        }
        failures = 0;
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof WorkbenchError && [401, 403, 404].includes(error.status)) { report(error); throw error; }
          failures += 1;
          update(runId, c => ({ ...c, activities: [...c.activities.filter(a => a.stage !== "connection"), { id: `${runId}:connection`, stage: "connection", status: "running", label: "正在恢复连接" }] }));
          await new Promise(resolve => window.setTimeout(resolve, Math.min(3000, failures * 350)));
          continue;
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        // A dropped stream is not a Run result.  Take one authoritative
        // snapshot before showing any interruption so a completed Run cannot
        // be rendered as a failure merely because the browser lost its pipe.
        try {
          const { snapshot } = await api.snapshot(conversationId, controller.signal);
          const current = snapshot.runs.find(run => run.run_id === runId);
          if (current) {
            await applyRun(conversationId, current);
            if (terminal.has(current.status) || current.status === "waiting_user") return;
          }
        } catch {
          // The original connection error is handled below with a neutral
          // status; authentication errors are still reported by the API layer.
        }
        if (error instanceof WorkbenchError && [401, 403, 404].includes(error.status)) report(error);
        update(runId, c => ({
          ...c,
          state: "interrupted",
          status: "interrupted",
          recovery: { mode: "none", can_continue: false, can_retry: false, message: "连接暂时中断，请重新打开对话确认进度。" },
          error: "连接暂时中断，请重新打开对话确认进度。",
        }));
      }
    } finally { if (controllers.current[runId] === controller) delete controllers.current[runId]; }
  }, [api, applyRun, client, scope, update, report]);
  const observe = useCallback((conversationId: string, runId: string) => {
    if (controllers.current[runId]) return;
    const controller = new AbortController(); controllers.current[runId] = controller;
    void follow(conversationId, runId, controller);
  }, [follow]);
  useEffect(() => {
    setNotice("");
    if (!id) return;
    const controller = new AbortController();
    void api.snapshot(id, controller.signal).then(async ({ snapshot }) => {
      for (const run of snapshot.runs.filter(r => ["running", "queued", "waiting_user"].includes(r.status))) {
        if (controller.signal.aborted) return;
        sequences.current[run.run_id] = run.last_sequence || 0;
        const all = detail.data?.linked_resources || [];
        await applyRun(id, run, all.filter(m => run.payload.attached_material_ids?.includes(m.id)));
        observe(id, run.run_id);
      }
    }).catch(error => { if (!controller.signal.aborted) report(error); });
    return () => { controller.abort(); Object.values(controllers.current).forEach(c => c.abort()); controllers.current = {}; };
    // Detail updates must not restart active subscriptions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, applyRun, id, observe, report]);
  function consumeDraft(conversationId: string, text: string, sent: Material[]) {
    setDrafts(current => { const next = { ...current }; for (const key of [conversationId, "new"]) if ((next[key] || "").trim() === text) next[key] = ""; return next; });
    const ids = new Set(sent.map(m => m.id));
    setAttachments(current => ({ ...current, [conversationId]: (current[conversationId] || []).filter(m => !ids.has(m.id)) }));
  }
  async function send(mode: "queue" | "steer" = "queue") {
    const question = draft.trim();
    const selectedMode = thinkingMode;
    if (!question || submitLock.current || uploads.some(u => !u.error) || question.length > 50000) return;
    submitLock.current = true; setNotice("");
    try {
      const conversationId = await ensureConversation(conversationTitle(question)); const sent = [...draftMaterials];
      let run: RunEnvelope;
      if (mode === "steer" && turn?.runId && turn.status === "running") {
        run = (await api.steer(turn.runId, conversationId, question, [], sent.map(m => m.id), taskFacts)).run;
        controllers.current[run.run_id]?.abort(); delete controllers.current[run.run_id];
      } else if (turn?.state === "waiting" && turn.runId && turn.inputRequest) {
        run = (await api.resume(turn.runId, conversationId, turn.inputRequest.requestId, question, sent.map(m => m.id))).run;
      } else {
        const key = crypto.randomUUID();
        const submit = () => api.submit(conversationId, question, [], lifetime.signal, key, undefined, sent.map(m => m.id), selectedMode, taskFacts);
        try { run = (await submit()).run; }
        catch (e) { if (e instanceof WorkbenchError && e.status === 0 && !lifetime.signal.aborted) run = (await submit()).run; else throw e; }
      }
      consumeDraft(conversationId, question, sent);
      await applyRun(conversationId, run, sent);
      observe(conversationId, run.run_id);
      if (id) {
        const firstQuestion = detail.data?.messages.find(message => message.role === "user" && message.content.trim())?.content || turn?.question || question;
        void nameConversation(conversationId, firstQuestion).catch(report);
      }
      void client.invalidateQueries({ queryKey: [...scope, "chat", conversationId] });
      void client.invalidateQueries({ queryKey: [...scope, "history"] });
    } catch (e) { report(e); }
    finally { submitLock.current = false; }
  }
  async function steerQueued(runId?: string) {
    if (!id || !runId || submitLock.current) return;
    const queued = currentCards.find(card => card.runId === runId && card.status === "queued");
    const active = currentCards.find(card => card.status === "running" && card.runId);
    if (!queued || !active?.runId) return;
    submitLock.current = true;
    try {
      // The queued Run has become the active instruction. Stop that queued
      // copy so it cannot execute a second time after the steered Run finishes.
      const stopped = (await api.stop(queued.runId, id)).run;
      controllers.current[queued.runId]?.abort();
      delete controllers.current[queued.runId];
      await applyRun(id, stopped, queued.materials);
      let steered: RunEnvelope;
      try {
        steered = (await api.steer(active.runId, id, queued.question, [], queued.materials.map(material => material.id), taskFacts)).run;
      } catch (error) {
        setDrafts(current => ({ ...current, [id]: [queued.question, current[id]].filter(Boolean).join("\n\n") }));
        setAttachments(current => ({ ...current, [id]: [...new Map([...(current[id] || []), ...queued.materials].map(material => [material.id, material])).values()] }));
        throw error;
      }
      controllers.current[active.runId]?.abort();
      delete controllers.current[active.runId];
      await applyRun(id, steered, queued.materials);
      observe(id, steered.run_id);
      setSuppressedRunIds(current => {
        const next = current.includes(queued.runId!) ? current : [...current, queued.runId!];
        try { sessionStorage.setItem(suppressedRunsKey, JSON.stringify(next)); } catch { /* memory state remains usable. */ }
        return next;
      });
      setCards(current => {
        const next = { ...current };
        delete next[queued.runId!];
        return next;
      });
      await refresh();
    } catch (e) {
      report(e);
    } finally {
      submitLock.current = false;
    }
  }
  function stageMaterial(conversationId: string, material: Material, remove = false) {
    setAttachments(current => ({ ...current, [conversationId]: remove ? (current[conversationId] || []).filter(m => m.id !== material.id) : [...(current[conversationId] || []).filter(m => m.id !== material.id), material] }));
  }
  async function uploadOne(entry: Upload) {
    try {
      const material = await api.uploadMaterial(entry.conversationId, entry.file);
      stageMaterial(entry.conversationId, { ...material, size_bytes: entry.file.size });
      setUploads(current => current.filter(u => u.key !== entry.key));
      await client.invalidateQueries({ queryKey: [...scope, "chat", entry.conversationId] });
      await client.invalidateQueries({ queryKey: [...scope, "resources"] });
    } catch (e) { setUploads(current => current.map(u => u.key === entry.key ? { ...u, error: e instanceof Error ? e.message : "上传未完成。" } : u)); report(e); }
  }
  async function uploadFiles(files: File[]) {
    if (!files.length || preparing) return;
    const valid = files.filter(f => f.size > 0 && f.size <= 30 * 1024 * 1024);
    if (!valid.length) { setNotice("请选择非空且不超过 30 MB 的文件。"); return; }
    setPreparing(true);
    try { const conversationId = await ensureConversation(); const entries = valid.map(file => ({ key: crypto.randomUUID(), conversationId, file })); setUploads(current => [...current, ...entries]); for (const entry of entries) await uploadOne(entry); }
    catch (e) { report(e); } finally { setPreparing(false); }
  }
  async function removeDraftMaterial(material: Material) {
    if (!id) return;
    if (await act(() => api.removeMaterial(id, material.id))) stageMaterial(id, material, true);
  }
  async function reloadTurn(runId = turn?.runId) { await refresh(); if (runId && id) { controllers.current[runId]?.abort(); delete controllers.current[runId]; const { run } = await api.run(runId, id); await applyRun(id, run); sequences.current[run.run_id] = run.last_sequence || 0; if (!terminal.has(run.status)) observe(id, run.run_id); } }
  async function continueRun(runId = turn?.runId) {
    if (!id || !runId) return;
    try { const { run } = await api.continueRun(runId, id); await applyRun(id, run); sequences.current[runId] = run.last_sequence || 0; observe(id, runId); } catch (e) { report(e); }
  }
  async function retryTurn(runId = turn?.runId) {
    if (!id || !runId || submitLock.current) return;
    submitLock.current = true;
    try { const source = (await api.run(runId, id)).run; if (source.status === "waiting_user") { const stopped = await api.stop(runId, id); await applyRun(id, stopped.run); } const retryFacts: TaskContextFacts = { skill_id: typeof source.payload.facts.skill_id === "string" ? source.payload.facts.skill_id : taskFacts.skill_id, case_id: typeof source.payload.facts.case_id === "string" ? source.payload.facts.case_id : taskFacts.case_id }; const { run } = await api.submit(id, source.payload.text, source.payload.material_ids, lifetime.signal, crypto.randomUUID(), runId, source.payload.attached_material_ids, source.payload.thinking_mode, retryFacts); await applyRun(id, run); observe(id, run.run_id); await refresh(); }
    catch (e) { report(e); } finally { submitLock.current = false; }
  }
  async function cancel(interrupt = false) {
    if (!turn?.runId || !id) return;
    try { const { run } = await (interrupt ? api.interrupt : api.stop)(turn.runId, id); controllers.current[run.run_id]?.abort(); delete controllers.current[run.run_id]; await applyRun(id, run); await refresh(); }
    catch (e) { report(e); }
  }
  async function deleteConversation(conversationId: string) {
    const { snapshot } = await api.snapshot(conversationId);
    const activeRuns = snapshot.runs.filter(run => ["running", "queued", "waiting_user"].includes(run.status));
    // Empty the queue before stopping active work, which could dispatch the next task.
    activeRuns.sort((left, right) => (left.status === "queued" ? 0 : 1) - (right.status === "queued" ? 0 : 1));
    for (const run of activeRuns) {
      await api.stop(run.run_id, conversationId);
      controllers.current[run.run_id]?.abort();
      delete controllers.current[run.run_id];
      delete sequences.current[run.run_id];
    }
    await api.deleteChat(conversationId);
    setCards(current => Object.fromEntries(Object.entries(current).filter(([, card]) => card.conversationId !== conversationId)));
    setDrafts(current => { const next = { ...current }; delete next[conversationId]; return next; });
    setAttachments(current => { const next = { ...current }; delete next[conversationId]; return next; });
  }
  const finishPresentation = useCallback(async (runId: string) => {
    if (!id) return;
    await client.invalidateQueries({ queryKey: [...scope, "chat", id] });
    setCards(current => { if (current[runId]?.status !== "completed") return current; const next = { ...current }; delete next[runId]; return next; });
  }, [client, id, scope]);
  const downloadArtifact = useCallback(async (runId: string, artifactId: string, name: string) => {
    if (!id) return;
    const blob = await api.downloadArtifact(runId, id, artifactId);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [api, id]);
  return { deleteConversation, thinkingMode, setThinkingMode, conversationMode, setConversationMode, modeChanging: false, visibleRuns: currentCards, suppressedRunIds, finishPresentation, downloadArtifact, api, scope, notice, setNotice, busy: turn?.state === "receiving", projects, history, resources, detail, act, refresh, report, projectFilter, setProjectFilter, draft, setDraft, draftMaterials, turn, queuedRuns, send, steerQueued, reloadTurn, retryTurn, continueRun, stop: () => cancel(), interrupt: () => cancel(true), steer: () => send("steer"), ensureConversation, uploads, uploadFiles, uploadOne, stageMaterial, removeDraftMaterial, dismissUpload: (key: string) => setUploads(current => current.filter(u => u.key !== key)) };
}
