import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import { ProtectedRoute, PublicOnlyRoute } from "@/auth/Guards";
import { AppShell } from "@/components/AppShell";
import { PublicLayout } from "@/pages/public/PublicLayout";

// Route-level code splitting: heavy pages load on demand (separate chunks).
const LandingPage = lazy(() =>
  import("@/pages/public/LandingPage").then((m) => ({ default: m.LandingPage })),
);
const PricingPage = lazy(() =>
  import("@/pages/public/PricingPage").then((m) => ({ default: m.PricingPage })),
);
const SecurityPage = lazy(() =>
  import("@/pages/public/SecurityPage").then((m) => ({ default: m.SecurityPage })),
);
const GuestConvertPage = lazy(() =>
  import("@/pages/public/GuestConvertPage").then((m) => ({ default: m.GuestConvertPage })),
);
const LoginPage = lazy(() =>
  import("@/pages/public/LoginPage").then((m) => ({ default: m.LoginPage })),
);
const RegisterPage = lazy(() =>
  import("@/pages/public/RegisterPage").then((m) => ({ default: m.RegisterPage })),
);
const VerifyEmailPage = lazy(() =>
  import("@/pages/public/VerifyEmailPage").then((m) => ({ default: m.VerifyEmailPage })),
);
const ForgotPasswordPage = lazy(() =>
  import("@/pages/public/ForgotPasswordPage").then((m) => ({ default: m.ForgotPasswordPage })),
);
const ResetPasswordPage = lazy(() =>
  import("@/pages/public/ResetPasswordPage").then((m) => ({ default: m.ResetPasswordPage })),
);
// Single dynamic public route: `/{ext}-converter` and `/{from}-to-{to}`.
// Static segments outrank it, so `/pricing`, `/convert`, `/login`, … still win.
const FormatRoutePage = lazy(() =>
  import("@/pages/public/FormatRoutePage").then((m) => ({ default: m.FormatRoutePage })),
);
const DashboardPage = lazy(() =>
  import("@/pages/app/DashboardPage").then((m) => ({ default: m.DashboardPage })),
);
const ConvertPage = lazy(() =>
  import("@/pages/app/ConvertPage").then((m) => ({ default: m.ConvertPage })),
);
const QueuePage = lazy(() =>
  import("@/pages/app/QueuePage").then((m) => ({ default: m.QueuePage })),
);
const HistoryPage = lazy(() =>
  import("@/pages/app/HistoryPage").then((m) => ({ default: m.HistoryPage })),
);
const FilesPage = lazy(() =>
  import("@/pages/app/FilesPage").then((m) => ({ default: m.FilesPage })),
);
const BillingPage = lazy(() =>
  import("@/pages/app/BillingPage").then((m) => ({ default: m.BillingPage })),
);
const SettingsPage = lazy(() =>
  import("@/pages/app/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);
const SupportPage = lazy(() =>
  import("@/pages/app/SupportPage").then((m) => ({ default: m.SupportPage })),
);

function RouteFallback() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-background">
      <span className="font-mono text-sm text-muted">Loading…</span>
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
      {/* Public marketing routes */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/security" element={<SecurityPage />} />
        <Route path="/convert" element={<GuestConvertPage />} />
        {/* Dynamic format routes, ranked below every static segment above. */}
        <Route path="/:slug" element={<FormatRoutePage />} />
      </Route>

      {/* Signed-out-only auth routes */}
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
      </Route>

      {/*
        Deliberately NOT behind `PublicOnlyRoute`.

        An activation link arrives from an email client and must work in
        whatever session the browser happens to be in. Inside that guard a user
        with a live session (a stale tab, a shared machine, a webmail link
        opened in the same browser) was redirected to the dashboard and their
        link silently did nothing. The page is idempotent and reveals nothing
        the holder of the link does not already have, so there is nothing to
        gate.

        The password-reset pair below has the same constraint for the same
        reason, and it matters just as much: `/reset-password` is reached from
        the link in the reset email, so inside `PublicOnlyRoute` a signed-in
        browser (a shared machine, a stale tab, the webmail client that opened
        the link) would be sent to the dashboard and the emailed link would
        silently never consume its token. `/forgot-password` is the destination
        that page's "request a new link" recovery action points at, so gating it
        would strand exactly the user who just needed it — and neither page
        reveals anything the holder of the link does not already have.
      */}
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

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
    </Suspense>
  );
}
