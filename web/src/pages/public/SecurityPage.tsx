import { Link } from "react-router-dom";
import {
  ArrowRight,
  Brain,
  CheckCircle,
  Eye,
  FileLock,
  Fingerprint,
  Gauge,
  Key,
  Keyhole,
  Lock,
  ShieldCheck,
  Trash,
} from "@phosphor-icons/react";
import { Button, Card } from "@/components/ui";
import { Stagger, Item, Reveal, PopIn } from "@/lib/motion";

/* ------------------------------------------------------------------ */
/* Feature card data — every claim is grounded in docs/security/       */
/* ------------------------------------------------------------------ */

const AUTH_DETAILS = [
  {
    icon: Key,
    title: "JWT access tokens",
    body: "Signed with HS256 and valid for 30 minutes, with a required exp and sub claim.",
  },
  {
    icon: Fingerprint,
    title: "Refresh tokens",
    body: "Longer-lived refresh tokens (7 days) mint new access tokens, guarded by a type claim so tokens can't be confused.",
  },
  {
    icon: Lock,
    title: "Argon2 password hashing",
    body: "Passwords are hashed with argon2 via pwdlib — never stored in plaintext.",
  },
  {
    icon: Keyhole,
    title: "Hashed API keys",
    body: "API keys are generated with 32 bytes of entropy, prefixed tr_, shown once, and only the SHA-256 hash is stored.",
  },
];

const LIFECYCLE_DETAILS = [
  {
    icon: Trash,
    title: "Guest & ownerless cleanup",
    body: "Guest conversion jobs and guest files are removed after 24 hours.",
  },
  {
    icon: Trash,
    title: "Temp object cleanup",
    body: "Temporary upload objects are purged after 1 hour.",
  },
  {
    icon: Eye,
    title: "Job history retention",
    body: "Full job history is archived after 30 days.",
  },
  {
    icon: CheckCircle,
    title: "GDPR-orientated deletion",
    body: "Retention and deletion are managed by the cleanup worker, with ownership checks keeping data isolated.",
  },
];

const ISMS_COVERAGE = [
  "API and presentation tier — JWT + API-key auth, tier-based authorization, rate limiting, security headers, SSE events, verified Stripe webhooks.",
  "Web application — the React SPA and its auth flows, file browser, job status, and billing portal.",
  "Worker pipeline — converter workers and the cleanup worker, plus the converter registry and its tooling.",
  "Data stores — the PostgreSQL schema, Redis streams and session caches, and the S3-compatible bucket.",
  "Cryptographic controls — at-rest AES-256-GCM encryption, per-user key derivation, JWT signing, hashed API keys, password hashing.",
  "Configuration & secrets — validated settings and boot-time production safeguards.",
  "Monitoring — audit logging, rate limiting, ownership checks, and path-traversal sanitization.",
];

/* ------------------------------------------------------------------ */

export function SecurityPage() {
  return (
    <div className="format-glyph-field">
      {/* Hero */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <Reveal className="max-w-3xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Security</p>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Your files, held to a higher standard.
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-muted">
            Transform is built on an ISO/IEC 27001:2022-aligned information security management
            system, backed by a documented audit pack. We treat your files with the care of systems
            we'd trust with our own.
          </p>
        </Reveal>
      </section>

      {/* Overview / commitment */}
      <section className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Commitment</p>
          <h2 className="font-display text-2xl font-semibold">A documented, auditable security posture</h2>
          <p className="mt-3 text-muted">
            Our ISMS covers every part of the platform we own and operate. The statement of
            applicability maps ISO/IEC 27001:2022 Annex A controls to the services in scope and
            is the core artifact of our audit pack.
          </p>
        </Reveal>

        <Stagger className="grid gap-3 sm:grid-cols-2">
          {ISMS_COVERAGE.map((item) => (
            <Item key={item} as="div">
              <div className="flex h-full items-start gap-3 rounded-xl border border-outline bg-surface p-5">
                <CheckCircle size={20} weight="duotone" className="mt-0.5 shrink-0 text-success" aria-hidden />
                <p className="text-sm text-on-background">{item}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Encryption at rest & in transit */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Encryption</p>
          <h2 className="font-display text-2xl font-semibold">Encrypted at rest, protected in transit</h2>
          <p className="mt-3 text-muted">
            Files are encrypted with AES-256-GCM before they touch object storage, so the ciphertext
            written for one owner can't be read by another even with storage access. In transit, TLS
            is terminated at the edge by the load balancer.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-2">
          <Item as="div">
            <Card className="h-full p-6" hover>
              <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                <FileLock size={20} weight="duotone" aria-hidden />
              </span>
              <h3 className="mb-1 font-display text-lg font-semibold">AES-256-GCM at rest</h3>
              <p className="text-sm text-muted">
                The worker encrypts files in chunks with AES-256-GCM before upload, and decrypts on
                download. Each file is encrypted under a key derived per-file with HKDF, bound to the
                user so ciphertext is isolated per owner.
              </p>
            </Card>
          </Item>
          <Item as="div">
            <Card className="h-full p-6" hover>
              <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                <Fingerprint size={20} weight="duotone" aria-hidden />
              </span>
              <h3 className="mb-1 font-display text-lg font-semibold">Per-user key derivation</h3>
              <p className="text-sm text-muted">
                A server-side master key drives per-user and per-file keys, so access to storage alone
                isn't enough to read a file.
              </p>
            </Card>
          </Item>
        </Stagger>

        <Reveal className="mt-4">
          <div className="rounded-xl border border-outline bg-surface p-5 text-sm">
            <p className="font-semibold text-on-background">On by default in production</p>
            <p className="mt-1 text-muted">
              At-rest encryption is a production requirement — settings validation refuses to start the
              service without a configured master key, so files are never stored plaintext in a
              production deployment.
            </p>
          </div>
        </Reveal>
      </section>

      {/* Authentication & authorization */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Access control</p>
          <h2 className="font-display text-2xl font-semibold">Authentication & authorization, by design</h2>
          <p className="mt-3 text-muted">
            Two authentication methods — JWT bearer tokens for the web app and API keys for
            programmatic clients. Ownership checks run at the application layer, not just in the UI,
            and tiers drive limits on both rate and file size.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-2">
          {AUTH_DETAILS.map(({ icon: Icon, title, body }) => (
            <Item key={title} as="div">
              <div className="flex h-full flex-col gap-3 rounded-xl border border-outline bg-surface p-6 transition-colors hover:border-primary/40">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                  <Icon size={20} weight="duotone" aria-hidden />
                </span>
                <h3 className="font-display text-base font-semibold">{title}</h3>
                <p className="text-sm text-muted">{body}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Rate limiting & abuse prevention */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Abuse prevention</p>
          <h2 className="font-display text-2xl font-semibold">Rate limiting & abuse prevention</h2>
          <p className="mt-3 text-muted">
            A Redis-backed sliding-window limiter bounds how many requests each client or API key can
            make, scaling with your subscription tier and tightening on sensitive endpoints.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-3">
          {[
            { label: "Guest", value: "10" },
            { label: "Free", value: "30" },
            { label: "Pro", value: "100" },
            { label: "Pro Plus", value: "200" },
            { label: "Enterprise", value: "500" },
            { label: "API key (default)", value: "1000" },
          ].map(({ label, value }) => (
            <Item key={label} as="div">
              <div className="flex flex-col rounded-xl border border-outline bg-surface p-5">
                <span className="text-xs font-medium uppercase tracking-wider text-muted">{label}</span>
                <span className="mt-1 font-display text-3xl font-semibold">{value}</span>
                <span className="text-xs text-muted">requests / minute</span>
              </div>
            </Item>
          ))}
        </Stagger>

        <Reveal className="mt-4">
          <p className="max-w-2xl text-sm text-muted">
            Exceeding a limit returns <code className="font-mono text-xs text-primary">429 Too Many Requests</code> with a{" "}
            <code className="font-mono text-xs text-primary">Retry-After</code> header. Auth endpoints
            get a stricter window (<code className="font-mono text-xs text-primary">RATE_LIMIT_AUTH=10</code>),
            and API-key limits are keyed on the key's SHA-256 hash so the raw key never appears in Redis.
          </p>
        </Reveal>
      </section>

      {/* Data lifecycle & privacy */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Privacy</p>
          <h2 className="font-display text-2xl font-semibold">Data lifecycle & privacy</h2>
          <p className="mt-3 text-muted">
            We don't keep files longer than we need to. A dedicated cleanup worker enforces retention
            windows that are short by default and GDPR-orientated.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-2">
          {LIFECYCLE_DETAILS.map(({ icon: Icon, title, body }) => (
            <Item key={title} as="div">
              <div className="flex h-full items-start gap-3 rounded-xl border border-outline bg-surface p-6 transition-colors hover:border-primary/40">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                  <Icon size={20} weight="duotone" aria-hidden />
                </span>
                <div>
                  <h3 className="font-display text-base font-semibold">{title}</h3>
                  <p className="mt-1 text-sm text-muted">{body}</p>
                </div>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Secure SDLC & auditability */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Engineering</p>
          <h2 className="font-display text-2xl font-semibold">Secure development & auditability</h2>
          <p className="mt-3 text-muted">
            Everything from the architecture to the runtime is built to be inspected and audited.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-2">
          {[
            {
              icon: Brain,
              title: "Clean architecture",
              body: "A domain/application/infrastructure split keeps business rules decoupled from transport and storage, making the security boundary auditable.",
            },
            {
              icon: ShieldCheck,
              title: "Secure coding practices",
              body: "Converters run via argument lists, never shell; SQL goes through the ORM; and a path-traversal guard sanitizes object keys.",
            },
            {
              icon: Eye,
              title: "Structured audit logs",
              body: "Security-relevant events are logged as JSON with a correlation ID and actor — never secrets, tokens, or file contents.",
            },
            {
              icon: Gauge,
              title: "Observability & probes",
              body: "Prometheus /metrics plus /health and /ready probes keep the platform observable and the workers honest.",
            },
          ].map(({ icon: Icon, title, body }) => (
            <Item key={title} as="div">
              <Card className="h-full p-6" hover>
                <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                  <Icon size={20} weight="duotone" aria-hidden />
                </span>
                <h3 className="mb-1 font-display text-base font-semibold">{title}</h3>
                <p className="text-sm text-muted">{body}</p>
              </Card>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Compliance posture callout */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal>
          <PopIn>
            <div className="rounded-2xl border border-outline bg-surface p-8">
              <div className="flex flex-col gap-8 lg:flex-row lg:items-start">
                <div className="flex-1">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Compliance posture</p>
                  <h2 className="font-display text-2xl font-semibold">ISO/IEC 27001:2022-aligned</h2>
                  <p className="mt-3 max-w-xl text-muted">
                    Our ISMS is documented in an audit pack that maps every in-scope Annex A control to
                    a concrete repository artifact, config key, or procedure. Where a control is not
                    fully in place, the gap is named — we'd rather be honest than over-claim.
                  </p>
                  <div className="mt-6 flex flex-wrap gap-3">
                    <Link to="/convert">
                      <Button>Convert a file now <ArrowRight size={18} /></Button>
                    </Link>
                    <Link to="/register">
                      <Button variant="secondary">Create a free account</Button>
                    </Link>
                  </div>
                </div>
                <div className="shrink-0 space-y-2 lg:w-72">
                  <div className="rounded-lg border border-success/30 bg-success/5 p-4">
                    <p className="font-semibold text-on-background">In place</p>
                    <ul className="mt-2 space-y-1 text-sm text-muted">
                      <li>· AES-256-GCM at-rest encryption</li>
                      <li>· JWT + hashed API keys</li>
                      <li>· Ownership &amp; tier-based authorization</li>
                      <li>· Rate limiting, CSP &amp; HSTS</li>
                      <li>· Structured audit logs &amp; metrics</li>
                    </ul>
                  </div>
                  <div className="rounded-lg border border-warning/30 bg-warning/5 p-4">
                    <p className="font-semibold text-on-background">Where we're honest</p>
                    <ul className="mt-2 space-y-1 text-sm text-muted">
                      <li>· TLS is terminated at the edge</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          </PopIn>
        </Reveal>
      </section>
    </div>
  );
}
