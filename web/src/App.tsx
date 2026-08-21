import { Routes, Route } from "react-router-dom";
import { ProtectedRoute, PublicOnlyRoute } from "@/auth/Guards";
import { AppShell } from "@/components/AppShell";
import { PublicLayout } from "@/pages/public/PublicLayout";
import { LandingPage } from "@/pages/public/LandingPage";
import { PricingPage } from "@/pages/public/PricingPage";
import { LoginPage } from "@/pages/public/LoginPage";
import { RegisterPage } from "@/pages/public/RegisterPage";
import { DashboardPage } from "@/pages/app/DashboardPage";
import { ConvertPage } from "@/pages/app/ConvertPage";
import { QueuePage } from "@/pages/app/QueuePage";
import { HistoryPage } from "@/pages/app/HistoryPage";
import { FilesPage } from "@/pages/app/FilesPage";
import { BillingPage } from "@/pages/app/BillingPage";
import { SettingsPage } from "@/pages/app/SettingsPage";
import { SupportPage } from "@/pages/app/SupportPage";

export default function App() {
  return (
    <Routes>
      {/* Public marketing routes */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/pricing" element={<PricingPage />} />
      </Route>

      {/* Signed-out-only auth routes */}
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
      </Route>

      {/* Authenticated app routes */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/app/dashboard" element={<DashboardPage />} />
          <Route path="/app/convert" element={<ConvertPage />} />
          <Route path="/app/queue" element={<QueuePage />} />
          <Route path="/app/history" element={<HistoryPage />} />
          <Route path="/app/files" element={<FilesPage />} />
          <Route path="/app/billing" element={<BillingPage />} />
          <Route path="/app/settings" element={<SettingsPage />} />
          <Route path="/app/support" element={<SupportPage />} />
        </Route>
      </Route>

      {/* Fallback */}
      <Route path="*" element={<PublicLayout />} />
    </Routes>
  );
}
