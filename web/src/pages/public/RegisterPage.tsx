import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { VerificationNotice } from "@/auth/VerificationNotice";
import { ApiError } from "@/api/client";
import { joinValidationMessages } from "@/lib/apiErrors";
import {
  MIN_PASSWORD_LENGTH,
  MIN_USERNAME_LENGTH,
  registrationServerErrors,
  validateRegistration,
  type RegisterFieldErrors,
} from "@/lib/registerForm";
import { Button, Field, Logo } from "@/components/ui";

export function RegisterPage() {
  const { register } = useAuth();
  const { error } = useToast();
  const navigate = useNavigate();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  /**
   * Per-field messages, from either the local rules or the API's 422 body.
   *
   * These render under the input they are about, which is the point: a field
   * error says "use at least 3 characters" next to the box that needs three
   * characters, where a toast said something about "a string" and left the user
   * to work out which of six inputs it meant.
   */
  const [fieldErrors, setFieldErrors] = useState<RegisterFieldErrors>({});
  /**
   * Set once the account exists but its email is unverified. The user is NOT
   * signed in at this point, so navigating to the app would bounce straight
   * back through `ProtectedRoute` — the flow ends here until they click the
   * link in their inbox.
   */
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);

  /** Clear one field's error as soon as the user edits it, so the message does not outlive the mistake. */
  function clearError(field: keyof RegisterFieldErrors) {
    setFieldErrors((current) => {
      if (current[field] === undefined) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;

    // Checked here rather than only at the API so the mistake is answered in the
    // form: no round trip, no toast, and the rule is in `lib/registerForm.ts`
    // beside the constants the field hints are rendered from.
    const found = validateRegistration({
      firstName,
      lastName,
      username,
      email,
      password,
      confirm,
    });
    setFieldErrors(found);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    try {
      const created = await register({
        // The trimmed values are what actually get submitted; a name of spaces
        // is not a name.
        username: username.trim(),
        email: email.trim(),
        password,
        first_name: firstName.trim(),
        last_name: lastName.trim(),
      });
      // The trimmed address, matching what was submitted: the panel shows where
      // the link went, so it must not display stray whitespace the user typed.
      setPendingEmail(created.email || email.trim());
    } catch (err) {
      // The API is the authority on its own rules, so anything it rejects is
      // shown exactly the way a local failure is — under the field it named.
      const validation = err instanceof ApiError ? err.validationErrors : undefined;
      const { fieldErrors: fromApi, unplaced } = registrationServerErrors(validation ?? []);

      if (Object.keys(fromApi).length > 0) {
        setFieldErrors(fromApi);
        // Anything the API complained about that is not one of this form's
        // fields would otherwise be invisible, so it falls back to a toast.
        if (unplaced.length > 0) error(joinValidationMessages(unplaced));
        return;
      }

      // No per-field detail to place (a network failure, a 409, a whole-body
      // error): the API's own message is the only explanation available.
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
          {/* Stacked on a phone, side by side once there is room — two 140px
              inputs at 320px would each be narrower than the label above them. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="First name"
              value={firstName}
              onChange={(e) => {
                setFirstName(e.target.value);
                clearError("first_name");
              }}
              autoComplete="given-name"
              maxLength={50}
              error={fieldErrors.first_name}
              required
            />
            <Field
              label="Last name"
              value={lastName}
              onChange={(e) => {
                setLastName(e.target.value);
                clearError("last_name");
              }}
              autoComplete="family-name"
              maxLength={50}
              error={fieldErrors.last_name}
              required
            />
          </div>
          <Field
            label="Username"
            value={username}
            onChange={(e) => {
              setUsername(e.target.value);
              clearError("username");
            }}
            autoComplete="username"
            // The rule is stated up front, not only after a rejection. It has to
            // match `UserCreateRequest.username` and `MIN_USERNAME_LENGTH`.
            hint={`At least ${MIN_USERNAME_LENGTH} characters. It can't be changed later.`}
            error={fieldErrors.username}
            required
          />
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              clearError("email");
            }}
            autoComplete="email"
            error={fieldErrors.email}
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              clearError("password");
              clearError("confirm");
            }}
            autoComplete="new-password"
            hint={`At least ${MIN_PASSWORD_LENGTH} characters`}
            error={fieldErrors.password}
            required
          />
          <Field
            label="Confirm password"
            type="password"
            value={confirm}
            onChange={(e) => {
              setConfirm(e.target.value);
              clearError("confirm");
            }}
            autoComplete="new-password"
            error={fieldErrors.confirm}
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
