import { useEffect, useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowRight,
  CheckCircle,
  ClockCounterClockwise,
  FileLock,
  Key,
  ShieldCheck,
} from "@phosphor-icons/react";
import { Button } from "@/components/ui";
import { ConverterCard } from "@/components/ConverterCard";
import { FormatCatalog } from "@/components/FormatCatalog";
import { FormatThumb } from "@/components/FormatThumb";
import { useConversionMap } from "@/lib/useConversionMap";
import { Stagger, Item, Reveal } from "@/lib/motion";

const SECURITY_FEATURES = [
  {
    icon: Key,
    title: "Authentication & access control",
    body: "Short-lived JWT access tokens, refresh tokens, hashed API keys, and per-resource ownership checks.",
  },
  {
    icon: FileLock,
    title: "Encryption at rest",
    body: "Files are encrypted with AES-256-GCM before they land in object storage, keyed per file.",
  },
  {
    icon: ShieldCheck,
    title: "Edge protections",
    body: "IP-based and per-key rate limiting, plus security headers like CSP and HSTS on every response.",
  },
  {
    icon: ClockCounterClockwise,
    title: "Data lifecycle & deletion",
    body: "Guest and ownerless files are auto-cleaned after 24 hours; your job history is kept for 30 days.",
  },
];

const FORMAT_GROUPS: { format: string; label: string }[] = [
  { format: "pdf", label: "Documents" },
  { format: "docx", label: "Word" },
  { format: "xlsx", label: "Spreadsheets" },
  { format: "png", label: "Images" },
  { format: "mp3", label: "Audio" },
  { format: "mp4", label: "Video" },
];

const STEPS = [
  { n: "1", title: "Add a file", body: "Drag in a PDF, DOCX, XLSX, image, or audio file." },
  { n: "2", title: "Choose the target", body: "Pick the format you want it to become." },
  { n: "3", title: "Download the result", body: "Watch it move through the queue, then grab it." },
];

/** The formats shown as a thumbnail strip under the hero copy. */
const HERO_FORMATS = ["pdf", "docx", "xlsx", "png", "mp3", "mp4"];

/** Canonical showcase pair, used until the live graph can confirm a better one. */
const DEFAULT_HERO_PAIR = { from: "pdf", to: "docx" };

export function LandingPage() {
  const navigate = useNavigate();
  const { map, sources, targetsFor, loading } = useConversionMap({ guest: true });

  // Show a pair that genuinely exists in the conversion graph. While the graph
  // loads (or if it is unavailable) fall back to the site's canonical
  // PDF → DOCX example — clicking it just opens the converter, which re-validates.
  const heroPair = useMemo(() => {
    if (!loading && map[DEFAULT_HERO_PAIR.from]?.includes(DEFAULT_HERO_PAIR.to)) {
      return DEFAULT_HERO_PAIR;
    }
    if (!loading) {
      const source = sources.find((s) => targetsFor(s).length > 0);
      const target = source ? targetsFor(source)[0] : undefined;
      if (source && target) return { from: source, to: target };
    }
    return DEFAULT_HERO_PAIR;
  }, [loading, map, sources, targetsFor]);

  const openConverter = () =>
    navigate("/convert", { state: { source: heroPair.from, target: heroPair.to } });

  // The footer links to `/#format-catalog`. React Router does not scroll to a
  // hash on its own, so honour it explicitly. `scrollIntoView` respects the
  // section's `scroll-mt-*`, keeping the heading clear of the sticky header.
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    const target = document.getElementById(hash.slice(1));
    target?.scrollIntoView({ behavior: "auto", block: "start" });
  }, [hash]);

  return (
    <div className="format-glyph-field">
      {/* Hero */}
      <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-20 sm:px-6 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="space-y-6"
        >
          <p className="inline-flex items-center gap-2 rounded-full border border-outline-strong px-3 py-1 text-xs font-medium uppercase tracking-wider text-muted">
            File conversion, done right
          </p>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Convert anything.
            <br />
            Watch it happen.
          </h1>
          <p className="max-w-md text-lg text-muted">
            Move files between formats with a live queue, real-time progress, and zero friction.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Link to="/convert">
              <Button size="lg">
                Convert now — no sign-up <ArrowRight size={18} />
              </Button>
            </Link>
            <Link to="/pricing">
              <Button size="lg" variant="secondary">
                See pricing
              </Button>
            </Link>
          </div>
          <p className="font-mono text-xs text-muted">
            Try it without an account, or{" "}
            <Link to="/register" className="text-primary hover:underline">
              sign up to keep your history
            </Link>
            .
          </p>
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Supported formats"
          >
            {HERO_FORMATS.map((format) => (
              <FormatThumb key={format} format={format} size="md" />
            ))}
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          className="flex items-center justify-center rounded-2xl border border-outline bg-surface/60 p-6 sm:p-10"
        >
          <ConverterCard
            from={heroPair.from}
            to={heroPair.to}
            onFromClick={openConverter}
            onToClick={openConverter}
          />
        </motion.div>
      </section>

      {/* Supported formats */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal>
          <h2 className="mb-6 font-display text-2xl font-semibold">Supported formats</h2>
        </Reveal>
        <Stagger className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {FORMAT_GROUPS.map(({ format, label }) => (
            <Item key={format} as="div">
              <div className="flex flex-col items-start gap-3 rounded-xl border border-outline bg-surface p-4 transition-colors hover:border-primary/40">
                <FormatThumb format={format} size="lg" />
                <span className="text-sm text-muted">{label}</span>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Format catalog — the richer expansion of the supported-formats cards,
          populated live from the real conversion graph. */}
      <FormatCatalog />

      {/* How it works */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal>
          <h2 className="mb-8 font-display text-2xl font-semibold">How it works</h2>
        </Reveal>
        <Stagger className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((s) => (
            <Item key={s.n} as="div">
              <div className="rounded-xl border border-outline bg-surface p-6 transition-colors hover:border-primary/40">
                <motion.div
                  className="mb-4 flex h-9 w-9 items-center justify-center rounded-lg bg-primary-container font-display text-lg font-semibold text-on-primary-container"
                  whileHover={{ scale: 1.1, rotate: -4 }}
                  transition={{ type: "spring", stiffness: 300, damping: 18 }}
                >
                  {s.n}
                </motion.div>
                <h3 className="mb-1 font-display text-lg font-semibold">{s.title}</h3>
                <p className="text-sm text-muted">{s.body}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

      {/* Security by design */}
      <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        <Reveal className="mb-8 max-w-2xl">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Security</p>
          <h2 className="font-display text-2xl font-semibold sm:text-3xl">Security by design</h2>
          <p className="mt-3 text-muted">
            Built on an ISO/IEC 27001:2022-aligned information security management system, so your
            files stay private, protected, and under your control.
          </p>
        </Reveal>

        <Stagger className="grid gap-4 sm:grid-cols-2">
          {SECURITY_FEATURES.map(({ icon: Icon, title, body }) => (
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

        <Reveal className="mt-8">
          <Link to="/security">
            <Button size="lg" variant="secondary">
              See how we secure your files <ArrowRight size={18} />
            </Button>
          </Link>
        </Reveal>
      </section>

      {/* CTA band */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <Reveal>
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-outline bg-surface p-10 text-center">
            <h2 className="font-display text-3xl font-semibold">Ready to transform?</h2>
            <p className="max-w-md text-muted">Convert a file right now — no account required. Or start free in under a minute.</p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/convert">
                <Button size="lg" variant="secondary">
                  Convert without an account <ArrowRight size={18} />
                </Button>
              </Link>
              <Link to="/register">
                <Button size="lg">
                  Get started <CheckCircle size={18} />
                </Button>
              </Link>
            </div>
          </div>
        </Reveal>
      </section>
    </div>
  );
}
