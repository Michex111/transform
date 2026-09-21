import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";

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

/** Only for signed-out visitors; sends signed-in users to the dashboard. */
export function PublicOnlyRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return null;
  if (isAuthenticated) return <Navigate to="/app/dashboard" replace />;
  return <Outlet />;
}
