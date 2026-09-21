import { useState, type FormEvent } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Envelope, BookOpen, Pulse, CaretDown } from "@phosphor-icons/react";
import { API_ORIGIN } from "@/api/client";
import { Button, Card, Field } from "@/components/ui";
import { Stagger, Item } from "@/lib/motion";

const FAQS = [
  { q: "How do I cancel?", a: "Go to Billing and choose Cancel subscription. You keep your tier until the period ends." },
  { q: "Where is my file stored?", a: "Files are stored in encrypted object storage and are only accessible to you." },
  { q: "Is my data encrypted?", a: "Yes. Files are encrypted at rest and in transit." },
  { q: "Do unused credits roll over?", a: "Credits are refreshed each monthly period for subscription plans." },
];

export function SupportPage() {
  const [open, setOpen] = useState<number | null>(0);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    // No support-ticket backend exists yet, so compose a prefilled email instead
    // of silently faking a successful send.
    const body = encodeURIComponent(message);
    const subjectLine = encodeURIComponent(subject.trim() || "Support request");
    window.location.href = `mailto:help@transform.app?subject=${subjectLine}&body=${body}`;
    setSubject("");
    setMessage("");
  }

  const CONTACTS = [
    {
      icon: Envelope,
      label: "Email support",
      detail: "help@transform.app",
      href: "mailto:help@transform.app",
      external: false,
    },
    {
      icon: BookOpen,
      label: "Documentation",
      detail: "Read the guides",
      // The API serves its docs and probes at its *root*, not under `/api`, and
      // the SPA is a separate static site — so a same-origin `/docs` would load
      // the SPA itself and render the router's empty catch-all page.
      href: `${API_ORIGIN}/docs`,
      external: true,
    },
    {
      icon: Pulse,
      label: "Status page",
      detail: "All systems operational",
      href: `${API_ORIGIN}/health`,
      external: true,
    },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Support</h1>
        <p className="text-sm text-muted">We're here to help.</p>
      </div>

      {/* Contact options */}
      <Stagger className="grid gap-4 sm:grid-cols-3">
        {CONTACTS.map(({ icon: Icon, label, detail, href, external }) => (
          <Item key={label} className="h-full">
            <a
              href={href}
              target={external ? "_blank" : undefined}
              rel={external ? "noreferrer" : undefined}
              className="block h-full"
            >
              <Card hover className="h-full p-5">
                <Icon size={22} className="mb-3 text-primary" />
                <p className="font-medium text-on-background">{label}</p>
                <p className="mt-0.5 text-sm text-muted">{detail}</p>
              </Card>
            </a>
          </Item>
        ))}
      </Stagger>

      {/* FAQ */}
      <Card className="overflow-hidden">
        <div className="border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Frequently asked</h2>
        </div>
        <ul className="divide-y divide-outline">
          {FAQS.map((f, i) => (
            <li key={f.q}>
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="flex w-full items-center justify-between px-5 py-4 text-left"
                aria-expanded={open === i}
              >
                <span className="text-sm font-medium text-on-background">{f.q}</span>
                <CaretDown size={16} className={`text-muted transition-transform ${open === i ? "rotate-180" : ""}`} />
              </button>
              <AnimatePresence initial={false}>
                {open === i && (
                  <motion.p
                    className="px-5 pb-4 text-sm text-muted"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  >
                    {f.a}
                  </motion.p>
                )}
              </AnimatePresence>
            </li>
          ))}
        </ul>
      </Card>

      {/* Report an issue */}
      <Card className="p-6">
        <h2 className="mb-4 font-display text-lg font-semibold">Report an issue</h2>
        <form onSubmit={submit} className="space-y-4">
          <Field
            label="Subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
          />
          <div className="space-y-1.5">
            <label htmlFor="message" className="block text-sm font-medium">
              Message
            </label>
            <textarea
              id="message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={4}
              required
              className="w-full rounded-lg border border-outline-strong bg-surface-variant px-3 py-2 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none"
              placeholder="Tell us what happened"
            />
          </div>
          <Button type="submit">Send</Button>
        </form>
      </Card>
    </div>
  );
}
