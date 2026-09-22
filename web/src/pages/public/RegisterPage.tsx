import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { VerificationNotice } from "@/auth/VerificationNotice";
import { Button, Field, Logo } from "@/components/ui";

export function RegisterPage() {
  const { register } = useAuth();
  const { error } = useToast();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  /**
   * Set once the account exists but its email is unverified. The user is NOT
   * signed in at this point, so navigating to the app would bounce straight
   * back through `ProtectedRoute` — the flow ends here until they click the
   * link in their inbox.
   */
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      error("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const created = await register({ username, email, password });
      setPendingEmail(created.email || email);
    } catch (err) {
      error(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  if (pendingEmail) {
    return (
      <div className="format-glyph-field flex min-h-[70vh] items-center justify-center px-4 py-12">
        <VerificationNotice
          email={pendingEmail}
          heading="Check your inbox"
          intro="Your account is created. We sent a verification link to"
          onBack={() => navigate("/login", { replace: true })}
        />
      </div>
    );
  }

  return (
    <div className="format-glyph-field flex min-h-[70vh] items-center justify-center px-4 py-12">
      <motion.div
        className="w-full max-w-sm rounded-2xl border border-outline bg-surface p-8"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 220, damping: 22 }}
      >
        <div className="mb-6 flex flex-col items-center text-center">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}>
            <Logo />
          </motion.div>
          <motion.h1
            className="mt-5 font-display text-2xl font-semibold"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.12 }}
          >
            Create your account
          </motion.h1>
          <motion.p
            className="mt-1 text-sm text-muted"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
          >
            Free to start. Convert in minutes.
          </motion.p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <Field
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            hint="At least 8 characters"
            required
          />
          <Field
            label="Confirm password"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
          />
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Creating account…" : "Create account"}
          </Button>
        </form>

        {/* Neither document exists yet, so these are inert, muted text (with
            `aria-disabled`) rather than dead `href="#"` links — the same
            treatment `PublicLayout`'s footer deliberately uses. */}
        <p className="mt-4 text-center text-xs text-muted">
          By continuing you agree to the{" "}
          <span className="cursor-default text-muted" title="Coming soon" aria-disabled="true">
            Terms
          </span>{" "}
          and{" "}
          <span className="cursor-default text-muted" title="Coming soon" aria-disabled="true">
            Privacy Policy
          </span>
          .
        </p>

        <div className="my-6 flex items-center gap-3 text-xs text-muted">
          <span className="h-px flex-1 bg-outline" />
          or
          <span className="h-px flex-1 bg-outline" />
        </div>

        <Link to="/login" className="block">
          <Button variant="secondary" className="w-full">
            Sign in
          </Button>
        </Link>

        <p className="mt-6 text-center">
          <Link to="/" className="text-sm text-muted hover:text-on-background">
            Back to home
          </Link>
        </p>
      </motion.div>
    </div>
  );
}
