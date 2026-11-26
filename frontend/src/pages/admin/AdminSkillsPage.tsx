import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { createAdminApi, type AdminUploadedSkill } from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { formatAdminDate, healthLabel, healthTone, InitialLoading, InlineError, PageHeader, RefreshButton, StatusPill } from "./AdminShared";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export default function AdminSkillsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<AdminUploadedSkill | null>(null);
  const [error, setError] = useState<string | null>(null);
  const skills = useQuery({ queryKey: ["admin", "skills"], queryFn: ({ signal }) => api.skills(signal), retry: false });

  const removal = useMutation({
    mutationFn: (skillId: string) => api.deleteUploadedSkill(skillId),
    onSuccess: async () => {
      setTarget(null);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["admin", "skills"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
    onError: cause => {
      setTarget(null);
      setError(cause instanceof Error ? cause.message : "删除没有完成。");
    },
  });

  const catalog = skills.data?.skills ?? [];
  const uploaded = skills.data?.uploaded ?? [];

  return (
    <div className="admin-route-page">
      <PageHeader title="技能" action={<RefreshButton loading={skills.isFetching} onClick={() => void skills.refetch()} />} />

      {error && <p className="admin-form-error" role="alert">{error}</p>}

      <section className="admin-panel" aria-labelledby="admin-skill-catalog">
        <header className="admin-panel-head"><h2 id="admin-skill-catalog">内置技能</h2></header>
        {skills.isPending ? <InitialLoading label="正在读取技能…" />
          : skills.isError ? <InlineError message={skills.error instanceof Error ? skills.error.message : "技能没有加载出来。"} onRetry={() => void skills.refetch()} />
            : catalog.length === 0 ? <p className="admin-empty">还没有技能。</p> : (
              <div className="admin-table-wrap is-flush">
                <table className="admin-table">
                  <thead>
                    <tr><th>技能</th><th>模式</th><th>版本</th><th>状态</th></tr>
                  </thead>
                  <tbody>
                    {catalog.map(skill => (
                      <tr key={skill.id}>
                        <td className="admin-cell-wide">
                          <strong className="admin-cell-title">{skill.name}</strong>
                          {skill.summary && <small className="admin-cell-sub">{skill.summary}</small>}
                        </td>
                        <td className="admin-cell-nowrap">{skill.mode_scope === "chat" ? "聊天" : "工作"}</td>
                        <td className="admin-cell-muted admin-cell-nowrap">{skill.version || "—"}</td>
                        <td><StatusPill tone={healthTone(skill.status)}>{healthLabel(skill.status)}</StatusPill></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </section>

      <section className="admin-panel" aria-labelledby="admin-skill-uploaded">
        <header className="admin-panel-head"><h2 id="admin-skill-uploaded">用户上传的技能</h2></header>
        {!skills.data ? <p className="admin-empty">{skills.isPending ? "读取中…" : "没有加载出来。"}</p>
          : uploaded.length === 0 ? <p className="admin-empty">还没有用户上传技能。</p> : (
            <div className="admin-table-wrap is-flush">
              <table className="admin-table">
                <thead>
                  <tr><th>技能</th><th>上传者</th><th>大小</th><th>更新时间</th><th>状态</th><th><span className="sr-only">操作</span></th></tr>
                </thead>
                <tbody>
                  {uploaded.map(skill => (
                    <tr key={skill.id}>
                      <td className="admin-cell-wide">
                        <strong className="admin-cell-title">{skill.name}</strong>
                        {skill.summary && <small className="admin-cell-sub">{skill.summary}</small>}
                      </td>
                      <td className="admin-cell-muted">{skill.owner_email ?? "（账户已不存在）"}</td>
                      <td className="admin-cell-muted admin-cell-nowrap">{skill.file_count} 个文件 · {formatSize(skill.size_bytes)}</td>
                      <td className="admin-cell-muted admin-cell-nowrap">{formatAdminDate(skill.updated_at)}</td>
                      <td><StatusPill tone={skill.active ? "ok" : "muted"}>{skill.active ? "使用中" : "用户已移除"}</StatusPill></td>
                      <td>
                        <button type="button" className="admin-row-action" onClick={() => setTarget(skill)}>
                          <Trash2 size={14} />
                          永久删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>

      <AlertDialog open={Boolean(target)} onOpenChange={open => { if (!open && !removal.isPending) setTarget(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>永久删除「{target?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              技能包和它的历史版本会一起删除，不能恢复。上传者再也用不了它，用过它的旧对话不受影响。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="admin-dialog-cancel" disabled={removal.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="admin-dialog-confirm"
              disabled={removal.isPending}
              onClick={event => {
                event.preventDefault();
                if (target) removal.mutate(target.id);
              }}
            >
              {removal.isPending ? "处理中…" : "永久删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
