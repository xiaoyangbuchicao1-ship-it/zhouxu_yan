import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { createAdminApi, type AdminCapability, type AdminModelGroup, type AdminModelGroupInput } from "@/lib/admin-api";

export type CapabilityChange = Partial<Pick<AdminCapability, "model_policy" | "allowed_models" | "group_id" | "skill_policy" | "can_upload_skill" | "material_quota_mb">> & {
  clear_material_quota?: boolean;
  clear_group?: boolean;
};

const CAPABILITIES_KEY = ["admin", "capabilities"] as const;
const MODEL_GROUPS_KEY = ["admin", "model-groups"] as const;

export const MODEL_POLICY_LABELS: Record<AdminCapability["model_policy"], string> = {
  default: "按默认与分组",
  allowlist: "仅限指定模型",
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
    onSuccess: async updated => {
      client.setQueryData<{ accounts: AdminCapability[] }>(
        CAPABILITIES_KEY,
        current => current
          ? { accounts: current.accounts.map(item => item.account_id === updated.account_id ? updated : item) }
          : current,
      );
      await client.invalidateQueries({ queryKey: MODEL_GROUPS_KEY });
    },
    onError: cause => setError(cause instanceof Error ? cause.message : "权限没有改成功。"),
  });
  return {
    query,
    error,
    saving: mutation.isPending,
    update: (accountId: string, change: CapabilityChange) => mutation.mutateAsync({ accountId, change }).then(() => true, () => false),
  };
}

export function useModelGroups(token: string) {
  const api = useMemo(() => createAdminApi(token), [token]);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: MODEL_GROUPS_KEY,
    queryFn: ({ signal }) => api.modelGroups(signal),
    retry: false,
  });

  async function syncAll() {
    await client.invalidateQueries({ queryKey: MODEL_GROUPS_KEY });
    await client.invalidateQueries({ queryKey: CAPABILITIES_KEY });
  }

  const createMutation = useMutation({
    mutationFn: (value: AdminModelGroupInput) => api.createModelGroup(value),
    onSuccess: syncAll,
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: Partial<AdminModelGroupInput> }) => api.updateModelGroup(id, value),
    onSuccess: syncAll,
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteModelGroup(id),
    onSuccess: syncAll,
  });

  return {
    query,
    groups: (query.data?.groups ?? []) as AdminModelGroup[],
    busy: createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
    create: createMutation.mutateAsync,
    update: (id: string, value: Partial<AdminModelGroupInput>) => updateMutation.mutateAsync({ id, value }),
    remove: deleteMutation.mutateAsync,
  };
}
