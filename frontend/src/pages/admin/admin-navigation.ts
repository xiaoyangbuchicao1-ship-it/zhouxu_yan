export interface AdminNavigationItem {
  label: string;
  to: string;
  end?: boolean;
}

// Only expose pages that already have a real API and a complete user journey.
// Models, corpus, skills, runs and operations join this list when their own
// vertical slices are connected; they are never shipped as empty menu shells.
export const adminNavigation: AdminNavigationItem[] = [
  { label: "总览", to: "/admin/overview", end: true },
  { label: "人员", to: "/admin/accounts" },
  { label: "能力与状态", to: "/admin/operations" },
  { label: "模型", to: "/admin/models" },
  { label: "审计日志", to: "/admin/audit" },
];

export function accountConversationPath(accountId: string): string {
  return `/admin/accounts/${encodeURIComponent(accountId)}/conversations`;
}

export function conversationDetailPath(accountId: string, conversationId: string): string {
  return `${accountConversationPath(accountId)}/${encodeURIComponent(conversationId)}`;
}
