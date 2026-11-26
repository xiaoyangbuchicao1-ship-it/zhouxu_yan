import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import AuthPage from "./pages/Auth";
import AdminLogin from "./pages/AdminLogin";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminOverviewPage from "./pages/admin/AdminOverviewPage";
import AdminAccountsPage from "./pages/admin/AdminAccountsPage";
import AdminAccountConversationsPage from "./pages/admin/AdminAccountConversationsPage";
import AdminConversationDetailPage from "./pages/admin/AdminConversationDetailPage";
import AdminSkillsPage from "./pages/admin/AdminSkillsPage";
import AdminModelsPage from "./pages/admin/AdminModelsPage";
import AdminAuditPage from "./pages/admin/AdminAuditPage";
import AdminDataPage from "./pages/admin/AdminDataPage";
import AdminToolsPage from "./pages/admin/AdminToolsPage";
import { lazy, Suspense } from "react";
import { AdminApiError } from "@/lib/admin-api";
import { saveSession } from "@/lib/auth-session";

const Workspace = lazy(() => import("./pages/Workspace"));

/* 管理台任何请求遇到登录过期，都回到登录页，不留一个半能用的页面。 */
function signOutWhenExpired(error: unknown) {
  if (error instanceof AdminApiError && error.status === 401 && error.code === "authentication_required") {
    saveSession(null, "登录已失效，请重新登录。");
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: signOutWhenExpired }),
  mutationCache: new MutationCache({ onError: signOutWhenExpired }),
});

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/register" element={<AuthPage mode="register" />} />
          <Route path="/account" element={<AuthPage mode="account" />} />
          <Route path="/admin/login" element={<AdminLogin />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<AdminOverviewPage />} />
            <Route path="accounts" element={<AdminAccountsPage />} />
            <Route path="accounts/:accountId/conversations" element={<AdminAccountConversationsPage />} />
            <Route path="accounts/:accountId/conversations/:conversationId" element={<AdminConversationDetailPage />} />
            <Route path="models" element={<AdminModelsPage />} />
            <Route path="skills" element={<AdminSkillsPage />} />
            <Route path="tools" element={<AdminToolsPage />} />
            <Route path="data" element={<AdminDataPage />} />
            <Route path="audit" element={<AdminAuditPage />} />
          </Route>
          <Route path="/workspace/*" element={<Suspense fallback={<div role="status" style={{ minHeight: "100dvh", display: "grid", placeItems: "center", background: "#080c10", fontSize: 16 }}>正在打开工作台…</div>}><Workspace /></Suspense>} />
          {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
