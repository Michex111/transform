import { useEffect, useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowRight,
  CheckCircle,
  ClockCounterClockwise,
  FileLock,
  FolderOpen,
  Key,
  MagnifyingGlass,
  Robot,
  ShieldCheck,
  Sparkle,
} from "@phosphor-icons/react";
import { API_ORIGIN } from "@/api/client";
import { Button } from "@/components/ui";
import { ConverterCard } from "@/components/ConverterCard";
import { FormatCatalog } from "@/components/FormatCatalog";
import { FormatThumb } from "@/components/FormatThumb";
import { CodeBlock } from "@/components/CodeBlock";
import { Section, SectionHeading } from "@/components/marketing/Section";
import { SeoHead } from "@/components/SeoHead";
import { useConversionMap } from "@/lib/useConversionMap";
import { Stagger, Item, Reveal } from "@/lib/motion";
import {
  breadcrumbJsonLd,
  organizationJsonLd,
  softwareApplicationJsonLd,
  websiteJsonLd,
} from "@/lib/seo";
import { DEFAULT_DESCRIPTION } from "@/lib/siteMeta";

const SECURITY_FEATURES = [
  {
    icon: Key,
    title: "Authentication & access control",
    body: "Short-lived JWT access tokens, refresh tokens, hashed API keys, and per-resource ownership checks.",
  },
  {
    icon: FileLock,
    title: "Encryption at rest",
    body: "Files are encrypted with AES-256-GCM before they land in object storage, under a per-user derived key.",
  },
  {
    icon: ShieldCheck,
    title: "Edge protections",
    body: "IP-based and per-key rate limiting, plus security headers like CSP and HSTS on every API response.",
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

/** What Transform AI can actually do — each line maps to a real tool. */
const AI_CAPABILITIES = [
  { icon: MagnifyingGlass, label: "Search your Drive", tool: "list_files" },
  { icon: Sparkle, label: "Summarise a document", tool: "summarize_file" },
  { icon: FileLock, label: "Read a document's text", tool: "read_file_text" },
  { icon: FolderOpen, label: "Convert a file", tool: "start_conversion" },
];

const DRIVE_FEATURES = [
  {
    icon: FileLock,
    title: "Encrypted by default",
    body: "Files are encrypted at rest with AES-256-GCM under a key derived for your account.",
  },
  {
    icon: FolderOpen,
    title: "Save in one click",
    body: "Keep a converted result in the Drive instead of downloading and losing it.",
  },
  {
    icon: Sparkle,
    title: "Ask AI about a file",
    body: "Point the assistant at something you already stored and summarise or convert it.",
  },
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
      <SeoHead
        meta={{ title: "AI-powered document platform", description: DEFAULT_DESCRIPTION, path: "/" }}
        jsonLd={[
          websiteJsonLd(),
          organizationJsonLd(),
          softwareApplicationJsonLd(DEFAULT_DESCRIPTION),
          breadcrumbJsonLd([{ name: "Home", path: "/" }]),
        ]}
      />

      {/* Hero */}
      <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-20 sm:px-6 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="space-y-6"
        >
          <p className="inline-flex items-center gap-2 rounded-full border border-outline-strong px-3 py-1 text-xs font-medium uppercase tracking-wider text-muted">
            <Sparkle size={14} weight="fill" className="text-primary" aria-hidden />
            AI-powered document platform
          </p>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Turn file work into
            <br />
            a conversation.
          </h1>
          <p className="max-w-md text-lg text-muted">
            Convert, store, and automate your documents with Transform's AI-powered platform. Ask
            in plain language — Transform finds the file and does the work.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Link to="/register">
              <Button size="lg">
                Try Transform free <ArrowRight size={18} />
              </Button>
            </Link>
            <a href="#ai">
              <Button size="lg" variant="secondary">
                <Sparkle size={18} /> Explore AI
              </Button>
            </a>
          </div>
          <p className="font-mono text-xs text-muted">
            No account needed to{" "}
            <Link to="/convert" className="text-link hover:underline">
              convert a file now
            </Link>{" "}
            — the free plan adds 5&nbsp;GB of storage and 50 conversions a month.
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

      {/* Transform AI */}
      <Section id="ai">
        <SectionHeading
          eyebrow="Transform AI"
          title="Ask. It finds the file and does the work."
          intro="Transform AI is a control layer over your documents, not a generic chatbot. It searches your Drive, reads and summarises files, and converts them — reporting what it did, with the real progress of every job."
        />
        <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-center">
          <Reveal>
            <ul className="space-y-3">
              {AI_CAPABILITIES.map(({ icon: Icon, label, tool }) => (
                <li
                  key={tool}
                  className="flex items-center gap-3 rounded-lg border border-outline bg-surface p-4"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                    <Icon size={18} weight="duotone" aria-hidden />
                  </span>
                  <span className="flex-1 text-sm font-medium">{label}</span>
                  <code className="font-mono text-xs text-muted">{tool}</code>
                </li>
              ))}
            </ul>
          </Reveal>

          {/* A storyboard of a real interaction: the prompt, the tools the
              assistant calls, and the answer. Illustrative of the shipped
              flow, not a live model. */}
          <Reveal delay={0.1}>
            <div className="rounded-2xl border border-outline bg-surface p-5 shadow-e2 sm:p-6">
              <p className="mb-4 text-xs font-medium uppercase tracking-wider text-muted">
                Transform AI
              </p>
              <div className="space-y-3">
                <ChatBubble role="user">Summarize my latest PDF</ChatBubble>
                <ToolStep label="Looking through your files" detail="list_files" />
                <ToolStep label="Summarising the document" detail="summarize_file" />
                <ChatBubble role="assistant">
                  Your latest PDF is the Q3 report. In short: revenue is up 12% on the quarter, the
                  two biggest drivers are renewals and the new self-serve plan, and the one open
                  risk is support capacity.
                </ChatBubble>
              </div>
            </div>
          </Reveal>
        </div>
      </Section>

      {/* Transform Drive */}
      <Section id="drive">
        <SectionHeading
          eyebrow="Transform Drive"
          title="A document vault, not a landfill"
          intro="Keep the files you have already transformed, ready for the next time you need them. Ask AI about them, convert them again, or download them — without re-uploading."
        />
        <Stagger className="mt-10 grid gap-4 sm:grid-cols-3">
          {DRIVE_FEATURES.map(({ icon: Icon, title, body }) => (
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
        <Reveal className="mt-6">
          <Link to="/register">
            <Button variant="secondary">
              Open your Drive <ArrowRight size={16} />
            </Button>
          </Link>
        </Reveal>
      </Section>

      {/* Formats — supported row, each card links to its real hub page */}
      <Section>
        <SectionHeading
          eyebrow="Convert"
          title="60+ formats, one queue"
          intro="Every conversion below runs on the same live queue with real-time progress. No sign-up required."
        />
        <Stagger className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {FORMAT_GROUPS.map(({ format, label }) => (
            <Item key={format} as="div">
              <Link
                to={`/${format}-converter`}
                className="flex flex-col items-start gap-3 rounded-xl border border-outline bg-surface p-4 transition-colors hover:border-primary/40"
              >
                <FormatThumb format={format} size="lg" />
                <span className="text-sm text-muted">{label}</span>
              </Link>
            </Item>
          ))}
        </Stagger>
      </Section>

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

      {/* Developers + MCP */}
      <Section>
        <SectionHeading
          eyebrow="Build on Transform"
          title="An API and an MCP server for everything above"
          intro="The same capabilities the app uses are available over HTTP, and to AI agents over MCP — with scoped, revocable permissions."
        />
        <div className="mt-10 grid gap-6 lg:grid-cols-2">
          <Reveal className="min-w-0">
            <div className="flex h-full flex-col gap-4 rounded-2xl border border-outline bg-surface p-6">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                <Key size={20} weight="duotone" aria-hidden />
              </span>
              <h3 className="font-display text-xl font-semibold">Build with Transform</h3>
              <p className="text-sm text-muted">
                Convert files and manage documents programmatically with an authenticated REST API
                and API keys.
              </p>
              <CodeBlock
                language="bash"
                label="REST API"
                code={`curl -X POST ${API_ORIGIN}/api/conversions/jobs \\\n  -H "Authorization: Bearer $TRANSFORM_API_KEY" \\\n  -d '{"file_id": "file_123", "target_format": "pdf"}'`}
              />
              <Link to="/developers" className="mt-auto">
                <Button variant="secondary">
                  Developer docs <ArrowRight size={16} />
                </Button>
              </Link>
            </div>
          </Reveal>
          <Reveal delay={0.08} className="min-w-0">
            <div className="flex h-full flex-col gap-4 rounded-2xl border border-outline bg-surface p-6">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
                <Robot size={20} weight="duotone" aria-hidden />
              </span>
              <h3 className="font-display text-xl font-semibold">
                Give AI agents document capabilities
              </h3>
              <p className="text-sm text-muted">
                Connect MCP-compatible agents so they can find, convert, and save documents — with
                explicit scopes you approve and can revoke at any time.
              </p>
              <ul className="space-y-2 text-sm text-muted">
                {["documents.read", "documents.convert", "documents.write"].map((scope) => (
                  <li key={scope} className="flex items-center gap-2 font-mono text-xs">
                    <CheckCircle size={14} weight="fill" className="text-success" aria-hidden />
                    {scope}
                  </li>
                ))}
                <li className="flex items-center gap-2 font-mono text-xs text-muted">
                  <span
                    className="block h-3.5 w-3.5 rounded-sm border border-outline-strong"
                    aria-hidden
                  />
                  documents.delete — opt-in only
                </li>
              </ul>
              <Link to="/mcp" className="mt-auto">
                <Button variant="secondary">
                  Explore MCP <ArrowRight size={16} />
                </Button>
              </Link>
            </div>
          </Reveal>
        </div>
      </Section>

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
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-outline bg-surface p-10 text-center shadow-e2">
            <h2 className="font-display text-3xl font-semibold">Ready to transform?</h2>
            <p className="max-w-md text-muted">
              Convert a file right now — no account required. Or start free and keep everything in
              your Drive.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/convert">
                <Button size="lg" variant="secondary">
                  Convert without an account <ArrowRight size={18} />
                </Button>
              </Link>
              <Link to="/register">
                <Button size="lg">
                  Try Transform free <CheckCircle size={18} />
                </Button>
              </Link>
            </div>
          </div>
        </Reveal>
      </section>
    </div>
  );
}

/** A chat bubble in the AI storyboard. */
function ChatBubble({
  role,
  children,
}: {
  role: "user" | "assistant";
  children: React.ReactNode;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <p
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${
          isUser
            ? "bg-primary text-on-primary"
            : "border border-outline bg-surface-variant text-on-surface"
        }`}
      >
        {children}
      </p>
    </div>
  );
}

/** A single tool step in the AI storyboard: a status line plus the tool name. */
function ToolStep({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="flex items-center gap-3 pl-1">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center">
        <CheckCircle size={16} weight="fill" className="text-success" aria-hidden />
      </span>
      <span className="flex-1 text-sm text-muted">{label}</span>
      <code className="font-mono text-xs text-muted">{detail}</code>
    </div>
  );
}
