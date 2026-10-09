import { Link, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { List, X } from "@phosphor-icons/react";
import { API_ORIGIN } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { ProfileMenu } from "@/components/ProfileMenu";
import { Logo } from "@/components/ui";
import { PUBLIC_HEADER_MENU } from "@/lib/profileMenu";

export function PublicLayout() {
  const location = useLocation();
  return (
    <div className="min-h-dvh bg-background text-on-background">
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
  const location = useLocation();
  const { isAuthenticated } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the mobile menu when navigating between pages.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // The public nav surfaces the whole platform, not just the converter. The
  // product is conversion + AI + Drive + a developer surface; a conversion-only
  // nav made the other three invisible to every prospect.
  const navLinks = [
    { to: "/convert", label: "Convert" },
    { to: "/developers", label: "Developers" },
    { to: "/mcp", label: "AI agents" },
    { to: "/pricing", label: "Pricing" },
    { to: "/security", label: "Security" },
  ];

  return (
    <header className="sticky top-0 z-30 border-b border-outline bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link to="/" aria-label="Transform home">
          <Logo />
        </Link>

        {/* Desktop nav — hidden below `md`, replaced by the mobile menu. */}
        <nav className="hidden items-center gap-4 md:flex" aria-label="Public">
          {navLinks.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className="text-sm font-medium text-muted hover:text-on-background"
            >
              {link.label}
            </Link>
          ))}
          {/* Signed-in visitors reach their account from here too; a guest keeps
              the sign-in / get-started pair unchanged. The purge stays off: an
              app-only action behind a marketing header is not discoverable, and
              the count would mean nothing in this context. */}
          {isAuthenticated ? (
            <ProfileMenu {...PUBLIC_HEADER_MENU} align="right" placement="down" />
          ) : (
            <>
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
            </>
          )}
        </nav>

        {/* `ml-auto` parks the avatar against the menu toggle — the toggle keeps
            the rightmost slot, because it still owns the nav links and taking
            that slot would cost access to them. Only rendered when signed in,
            so the guest header stays exactly as it was. */}
        {isAuthenticated && (
          <ProfileMenu {...PUBLIC_HEADER_MENU} align="right" placement="down" className="ml-auto md:hidden" />
        )}

        {/* Mobile menu toggle — only visible on small screens. */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          className="flex h-10 w-10 items-center justify-center rounded-lg text-on-background transition-colors hover:bg-surface-variant md:hidden"
        >
          {menuOpen ? <X size={22} /> : <List size={22} />}
        </button>
      </div>

      {/* Mobile menu — collapsible panel so the nav never overflows at 375px. */}
      {menuOpen && (
        <nav
          className="border-t border-outline bg-background px-4 py-3 md:hidden"
          aria-label="Public"
        >
          <div className="flex flex-col gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className="rounded-lg px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
              >
                {link.label}
              </Link>
            ))}
            {/* The sign-in pair is for visitors only. A signed-in reader opening
                this panel is already in, and the account itself is the avatar in
                the row above. */}
            {!isAuthenticated && (
              <div className="mt-2 flex flex-col gap-2">
                <Link
                  to="/login"
                  className="rounded-lg border border-outline-strong px-4 py-2 text-center text-sm font-semibold text-on-background transition-colors hover:bg-surface-variant"
                >
                  Sign in
                </Link>
                <Link
                  to="/register"
                  className="rounded-lg bg-primary px-4 py-2 text-center text-sm font-semibold text-on-primary transition-colors hover:bg-primary/90"
                >
                  Get started
                </Link>
              </div>
            )}
          </div>
        </nav>
      )}
    </header>
  );
}

function PublicFooter() {
  const linkClass = "text-sm text-muted hover:text-on-background";
  type FooterLink = { label: string; to?: string; href?: string };
  const Col = ({ title, links }: { title: string; links: FooterLink[] }) => (
    <div>
      <p className="mb-3 text-sm font-semibold text-on-background">{title}</p>
      <ul className="space-y-2">
        {links.map((it) => (
          <li key={it.label}>
            {it.to ? (
              <Link to={it.to} className={linkClass}>
                {it.label}
              </Link>
            ) : it.href ? (
              /* An absolute URL (the API reference and status live on the API
                 origin, not this host). Opened in a new tab like the Support
                 page's links, so the SPA is not replaced. */
              <a href={it.href} target="_blank" rel="noreferrer" className={linkClass}>
                {it.label}
              </a>
            ) : (
              /* No real route exists yet — render as inert, non-clickable text
                 instead of a dead `href="#"` link. */
              <span
                className="cursor-default text-sm text-muted"
                title="Coming soon"
                aria-disabled="true"
              >
                {it.label}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <footer className="border-t border-outline">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-8 px-4 py-12 sm:grid-cols-3 sm:px-6 lg:grid-cols-5">
        <Col
          title="Product"
          links={[
            { label: "Convert", to: "/convert" },
            { label: "Pricing", to: "/pricing" },
            { label: "Security", to: "/security" },
            { label: "Format catalog", to: "/#format-catalog" },
          ]}
        />
        <Col
          title="Platform"
          links={[
            { label: "Developers", to: "/developers" },
            { label: "AI agents", to: "/mcp" },
            { label: "API reference", href: `${API_ORIGIN}/docs` },
          ]}
        />
        {/* Real conversion pages, so the public site links into the format
            layer instead of leaving it orphaned from `/`. */}
        <Col
          title="Popular conversions"
          links={[
            { label: "PDF converter", to: "/pdf-converter" },
            { label: "DOCX converter", to: "/docx-converter" },
            { label: "JPG converter", to: "/jpg-converter" },
            { label: "MP3 converter", to: "/mp3-converter" },
          ]}
        />
        <Col
          title="Company"
          links={[
            { label: "About" },
            { label: "Blog" },
            { label: "Contact" },
            { label: "Status", href: `${API_ORIGIN}/health` },
          ]}
        />
        <Col
          title="Legal"
          links={[
            { label: "Privacy" },
            { label: "Terms" },
            { label: "GDPR" },
            { label: "Security", to: "/security" },
          ]}
        />
      </div>
      <div className="border-t border-outline">
        <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
          <p className="text-sm text-muted">
            Transform is an AI-powered document platform — convert, store, and automate your
            documents.
          </p>
        </div>
      </div>
    </footer>
  );
}
