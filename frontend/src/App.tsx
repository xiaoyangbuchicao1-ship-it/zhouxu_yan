import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import AuthPage from "./pages/Auth";
import AdminLogin from "./pages/AdminLogin";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminOverviewPage from "./pages/admin/AdminOverviewPage";
import AdminAccountsPage from "./pages/admin/AdminAccountsPage";
import AdminAccountConversationsPage from "./pages/admin/AdminAccountConversationsPage";
import AdminConversationDetailPage from "./pages/admin/AdminConversationDetailPage";
import { lazy, Suspense } from "react";

const Workspace = lazy(() => import("./pages/Workspace"));

const queryClient = new QueryClient();

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
            <Route index element={<AdminOverviewPage />} />
            <Route path="overview" element={<AdminOverviewPage />} />
            <Route path="accounts" element={<AdminAccountsPage />} />
            <Route path="accounts/:accountId/conversations" element={<AdminAccountConversationsPage />} />
            <Route path="accounts/:accountId/conversations/:conversationId" element={<AdminConversationDetailPage />} />
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
