import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { createAdminApi, type AdminCapability } from "@/lib/admin-api";

export type CapabilityChange = Partial<Pick<AdminCapability, "model_policy" | "allowed_models" | "skill_policy" | "can_upload_skill" | "material_quota_mb">> & {
  clear_material_quota?: boolean;
};

const CAPABILITIES_KEY = ["admin", "capabilities"] as const;

export const MODEL_POLICY_LABELS: Record<AdminCapability["model_policy"], string> = {
  default: "全部已启用模型",
  allowlist: "指定模型",
  disabled: "关闭",
};

export const SKILL_POLICY_LABELS: Record<AdminCapability["skill_policy"], string> = {
  default: "全部技能",
  builtin_only: "仅内置技能",
  disabled: "关闭",
};

export function useCapabilities(token: string) {
  const api = useMemo(() => createAdminApi(token), [token]);
  const client = useQueryClient();
  const [error, setError] = useState("");
  const query = useQuery({
    queryKey: CAPABILITIES_KEY,
    queryFn: ({ signal }) => api.capabilities(signal),
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: ({ accountId, change }: { accountId: string; change: CapabilityChange }) => api.updateCapability(accountId, change),
    onMutate: () => setError(""),
    onSuccess: updated => client.setQueryData<{ accounts: AdminCapability[] }>(
      CAPABILITIES_KEY,
      current => current
        ? { accounts: current.accounts.map(item => item.account_id === updated.account_id ? updated : item) }
        : current,
    ),
    onError: cause => setError(cause instanceof Error ? cause.message : "权限没有改成功。"),
  });
  return {
    query,
    error,
    saving: mutation.isPending,
    update: (accountId: string, change: CapabilityChange) => mutation.mutateAsync({ accountId, change }).then(() => true, () => false),
  };
}
