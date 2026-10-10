import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { safeReturnPath } from "@/lib/returnTo";

/** Requires an authenticated user; redirects to /login otherwise. */
export function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <span className="font-mono text-sm text-muted">Loading…</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    // Preserve the query string too: Stripe returns users to paths like
    // `/app/billing?credits=success`, and BillingPage reads those params to
    // show the payment result. Dropping them would hide the confirmation
    // when the session expires while the user is on the hosted Checkout page.
    return (
      <Navigate
        to="/login"
        state={{ from: `${location.pathname}${location.search}` }}
        replace
      />
    );
  }

  return <Outlet />;
}

/**
 * Only for signed-out visitors.
 *
 * Sends an authenticated visitor to the destination `ProtectedRoute` recorded,
 * rather than to a fixed page. Hardcoding the dashboard here silently discarded
 * that destination: this guard redirects the instant authentication succeeds,
 * which happens *while the sign-in page is still navigating to `from`*, so this
 * target won the race every time.
 *
 * The symptom that surfaced it was an AI application's consent screen becoming
 * unreachable — a harness opens a cold browser tab at `/app/authorize?...`, the
 * visitor signs in, and both the page and the request vanished, so the
 * connection could never complete. Stripe's `?credits=success` return and every
 * signed-out deep link broke the same way.
 */
export function PublicOnlyRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) return null;
  if (isAuthenticated) {
    // Same shape `ProtectedRoute` writes when it bounces a visitor here.
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={safeReturnPath(from)} replace />;
  }
  return <Outlet />;
}
