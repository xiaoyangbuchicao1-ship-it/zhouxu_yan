import { Check, ShieldCheck, UserRound, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createAdminApi } from "@/lib/admin-api";
import { InlineError, InitialLoading, LoadError, PageHeader, RefreshButton } from "./AdminShared";
import { useAdminOutlet } from "./AdminLayout";

export default function AdminOverviewPage() {
  const { token } = useAdminOutlet();
  const query = useQuery({
    queryKey: ["admin", "overview"],
    queryFn: () => createAdminApi(token).overview(),
    staleTime: 15_000,
    retry: false,
    placeholderData: previous => previous,
  });
  const overview = query.data?.overview;
  return <div className="admin-route-page">
    <PageHeader eyebrow="今日管理" title="总览" description="先处理需要关注的事项。" action={<RefreshButton loading={query.isFetching} onClick={() => void query.refetch()} />} />
    {!overview && query.isPending ? <InitialLoading label="正在读取总览…" />
      : !overview ? <LoadError message={query.error instanceof Error ? query.error.message : "总览暂时不可用。"} onRetry={() => void query.refetch()} />
      : <>
        {query.isError && <InlineError message={query.error instanceof Error ? query.error.message : "刷新没有完成。"} onRetry={() => void query.refetch()} />}
        <section className="admin-metrics" aria-label="账户概览">
          <Metric icon={<Users size={17} />} label="全部账户" value={overview.total_accounts} />
          <Metric icon={<Check size={17} />} label="正常账户" value={overview.active_accounts} />
          <Metric icon={<UserRound size={17} />} label="已停用" value={overview.disabled_accounts} />
          <Metric icon={<ShieldCheck size={17} />} label="管理员" value={overview.admin_accounts} />
        </section>
        <section className="admin-focus-list" aria-labelledby="admin-focus-title">
          <div><p>人员</p><h2 id="admin-focus-title">账户与会话</h2></div>
          <p>{overview.disabled_accounts > 0 ? `${overview.disabled_accounts} 个账户已停用` : "所有账户状态正常"}</p>
          <Link to="/admin/accounts">查看人员</Link>
        </section>
      </>}
  </div>;
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return <div className="admin-metric"><span className="admin-metric-icon">{icon}</span><span className="admin-metric-label">{label}</span><strong>{value.toLocaleString("zh-CN")}</strong></div>;
}
