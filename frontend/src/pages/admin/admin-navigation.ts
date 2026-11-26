export interface AdminNavigationItem {
  label: string;
  to: string;
  end?: boolean;
}

export interface AdminNavigationGroup {
  label: string;
  items: AdminNavigationItem[];
}

// Pages are grouped by what the administrator is looking after, not by when
// they were built.  Only pages with a real API and a complete journey appear.
export const adminNavigationGroups: AdminNavigationGroup[] = [
  { label: "运行", items: [{ label: "总览", to: "/admin/overview", end: true }] },
  { label: "用户", items: [{ label: "账户", to: "/admin/accounts" }] },
  {
    label: "能力与数据",
    items: [
      { label: "模型", to: "/admin/models" },
      { label: "技能", to: "/admin/skills" },
      { label: "工具", to: "/admin/tools" },
      { label: "数据库", to: "/admin/data" },
    ],
  },
  { label: "安全", items: [{ label: "审计", to: "/admin/audit" }] },
];

export const adminNavigation: AdminNavigationItem[] = adminNavigationGroups.flatMap(group => group.items);

export function accountConversationPath(accountId: string): string {
  return `/admin/accounts/${encodeURIComponent(accountId)}/conversations`;
}

export function conversationDetailPath(accountId: string, conversationId: string): string {
  return `${accountConversationPath(accountId)}/${encodeURIComponent(conversationId)}`;
}
