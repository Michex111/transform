import { Link, Outlet, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { Logo } from "@/components/ui";

export function PublicLayout() {
  const location = useLocation();
  return (
    <div className="min-h-screen bg-background text-on-background">
      <PublicHeader />
      {/* Only the page content animates; header + footer stay mounted.
          Mounting-only animation (no AnimatePresence mode="wait") so pages
          always render reliably. */}
      <motion.div
        key={location.pathname}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      >
        <Outlet />
      </motion.div>
      <PublicFooter />
    </div>
  );
}

function PublicHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-outline bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link to="/" aria-label="Transform home">
          <Logo />
        </Link>
        <nav className="flex items-center gap-4" aria-label="Public">
          <Link to="/convert" className="text-sm font-medium text-muted hover:text-on-background">
            Convert
          </Link>
          <Link to="/pricing" className="text-sm font-medium text-muted hover:text-on-background">
            Pricing
          </Link>
          <Link
            to="/security"
            className="text-sm font-medium text-muted hover:text-on-background"
            aria-label="Security"
          >
            Security
          </Link>
          <Link
            to="/login"
            className="rounded-lg border border-outline-strong px-4 py-2 text-sm font-semibold text-on-background transition-colors hover:bg-surface-variant"
          >
            Sign in
          </Link>
          <Link
            to="/register"
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-colors hover:bg-primary/90"
          >
            Get started
          </Link>
        </nav>
      </div>
    </header>
  );
}

function PublicFooter() {
  const linkClass = "text-sm text-muted hover:text-on-background";
  const Col = ({ title, links }: { title: string; links: { label: string; to?: string }[] }) => (
    <div>
      <p className="mb-3 text-sm font-semibold text-on-background">{title}</p>
      <ul className="space-y-2">
        {links.map((it) => (
          <li key={it.label}>
            {it.to ? (
              <Link to={it.to} className={linkClass}>
                {it.label}
              </Link>
            ) : (
              <a href="#" className={linkClass}>
                {it.label}
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <footer className="border-t border-outline">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-8 px-4 py-12 sm:grid-cols-4 sm:px-6">
        <Col
          title="Product"
          links={[
            { label: "Convert", to: "/convert" },
            { label: "Queue", to: "/app/queue" },
            { label: "Pricing", to: "/pricing" },
            { label: "API keys" },
          ]}
        />
        <Col title="Company" links={[{ label: "About" }, { label: "Blog" }, { label: "Careers" }, { label: "Contact" }]} />
        <Col
          title="Legal"
          links={[
            { label: "Privacy" },
            { label: "Terms" },
            { label: "Security", to: "/security" },
            { label: "GDPR" },
          ]}
        />
        <div>
          <p className="mb-3 text-sm font-semibold text-on-background">Transform</p>
          <p className="text-sm text-muted">
            Fast, dependable file conversion with a live queue.
          </p>
        </div>
      </div>
    </footer>
  );
}
