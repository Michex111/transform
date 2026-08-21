import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, CheckCircle } from "@phosphor-icons/react";
import { Button, FormatChip, FormatMorph } from "@/components/ui";
import { Stagger, Item, Reveal } from "@/lib/motion";

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

export function LandingPage() {
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
            <Link to="/register">
              <Button size="lg">
                Start converting <ArrowRight size={18} />
              </Button>
            </Link>
            <Link to="/pricing">
              <Button size="lg" variant="secondary">
                See pricing
              </Button>
            </Link>
          </div>
          <p className="font-mono text-xs text-muted">
            PDF · DOCX · XLSX · images · audio · video
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          className="flex items-center justify-center rounded-2xl border border-outline bg-surface/60 p-10"
        >
          <FormatMorph from="pdf" to="docx" size="lg" animated />
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
              <div className="flex flex-col items-start gap-2 rounded-xl border border-outline bg-surface p-4 transition-colors hover:border-primary/40">
                <FormatChip format={format} />
                <span className="text-sm text-muted">{label}</span>
              </div>
            </Item>
          ))}
        </Stagger>
      </section>

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

      {/* CTA band */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <Reveal>
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-outline bg-surface p-10 text-center">
            <h2 className="font-display text-3xl font-semibold">Ready to transform?</h2>
            <p className="max-w-md text-muted">Start free in under a minute. No credit card required.</p>
            <Link to="/register">
              <Button size="lg">
                Get started <CheckCircle size={18} />
              </Button>
            </Link>
          </div>
        </Reveal>
      </section>
    </div>
  );
}
