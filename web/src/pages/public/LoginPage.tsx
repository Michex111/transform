import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { VerificationNotice } from "@/auth/VerificationNotice";
import { ApiError } from "@/api/client";
import { EMAIL_NOT_VERIFIED } from "@/api/types";
import { Button, Field, Logo } from "@/components/ui";

export function LoginPage() {
  const { login } = useAuth();
  const { error } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState(
    () => (location.state as { username?: string } | null)?.username ?? "",
  );
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  /**
   * Set when the API refuses sign-in solely because the address is unverified.
   *
   * This must NOT be treated like a bad password: the credentials were correct,
   * so clearing them and showing a generic error would leave the user with no
   * idea that a link is sitting in their inbox.
   */
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/app/dashboard";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === EMAIL_NOT_VERIFIED) {
        // The API includes the account's own address on this response (it is
        // only reachable with a correct username *and* password), which is what
        // makes the "resend" button work when the user signed in by username.
        const address = err.details?.email;
        if (typeof address === "string" && address) {
          setUnverifiedEmail(address);
        } else {
          // Should be unreachable. Rather than render a panel with no address
          // to resend to, surface the API's explanation and leave the form
          // usable.
          error(err.message);
        }
      } else {
        error(err instanceof Error ? err.message : "Sign in failed");
      }
    } finally {
      setBusy(false);
    }
  }

  if (unverifiedEmail) {
    return (
      <div className="format-glyph-field flex min-h-[70vh] items-center justify-center px-4 py-12">
        <VerificationNotice
          email={unverifiedEmail}
          heading="Verify your email"
          intro="Your account is not activated yet. We sent a verification link to"
          onBack={() => setUnverifiedEmail(null)}
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
            Welcome back
          </motion.h1>
          <motion.p
            className="mt-1 text-sm text-muted"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
          >
            Sign in to keep converting.
          </motion.p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          {/* Labelled "Username or email" because the API accepts either. It
              used to accept a username only, which meant typing the address the
              app itself shows you (the shell prints it, and the register form
              asks for it) failed with a generic "Incorrect username or
              password" — indistinguishable from a wrong password. */}
          <Field
            label="Username or email"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            hint="Either works — whichever you remember."
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          {/* Right-aligned immediately under the field, which is where the eye
              looks after failing to remember a password. */}
          <div className="flex justify-end">
            <Link to="/forgot-password" className="text-sm text-primary hover:underline">
              Forgot password?
            </Link>
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm">
          {/* This affordance links to the registration form, so it must say so:
              it used to be labelled "Forgot password?" while pointing at
              `/register`. A real recovery flow now exists, so this one is
              honestly labelled "Create an account" and the actual
              forgot-password link sits under the password field above. */}
          <Link to="/register" className="text-primary hover:underline">
            Create an account
          </Link>
        </p>

        <div className="my-6 flex items-center gap-3 text-xs text-muted">
          <span className="h-px flex-1 bg-outline" />
          or
          <span className="h-px flex-1 bg-outline" />
        </div>

        <Link to="/register" className="block">
          <Button variant="secondary" className="w-full">
            Create an account
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
