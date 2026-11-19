import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Layout } from "./components/Layout";
import { ErrorNotice, Spinner } from "./components/ui";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useConfig } from "./hooks/useApi";
import { tokenStore } from "./services/api";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { UploadPage } from "./pages/UploadPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { DocumentPage } from "./pages/DocumentPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { ReviewEditorPage } from "./pages/ReviewEditorPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";

export default function App() {
  const config = useConfig();
  const queryClient = useQueryClient();
  const [signedIn, setSignedIn] = useState(() => Boolean(tokenStore.get()));

  useEffect(() => {
    const onUnauthorized = () => {
      setSignedIn(false);
      queryClient.clear();
    };
    window.addEventListener("invoice-ai:unauthorized", onUnauthorized);
    return () => window.removeEventListener("invoice-ai:unauthorized", onUnauthorized);
  }, [queryClient]);

  if (config.isLoading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Starting" />
      </div>
    );
  }
  const authRequired = config.data?.auth_enabled ?? true;
  if (authRequired && !signedIn) {
    return <LoginPage onSignedIn={() => setSignedIn(true)} />;
  }

  return (
    <ErrorBoundary fallback={<div className="p-8"><ErrorNotice title="Something on this page failed to load" message="Reload the page. If it keeps happening, check the server logs." /></div>}>
    <BrowserRouter>
      <Routes>
        <Route
          element={
            <Layout
              onSignOut={() => {
                queryClient.clear();
                setSignedIn(false);
              }}
            />
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="documents/:id" element={<DocumentPage />} />
          <Route path="review" element={<ReviewQueuePage />} />
          <Route path="review/:id" element={<ReviewEditorPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
    </ErrorBoundary>
  );
}
