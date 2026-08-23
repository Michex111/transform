import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  ChartBar,
  ArrowsClockwise,
  List,
  ClockCounterClockwise,
  FolderOpen,
  CreditCard,
  GearSix,
  Lifebuoy,
  SignOut,
  DotsThree,
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { Logo } from "@/components/ui";

const NAV = [
  { to: "/app/dashboard", label: "Dashboard", icon: ChartBar },
  { to: "/app/convert", label: "Convert", icon: ArrowsClockwise },
  { to: "/app/queue", label: "Queue", icon: List },
  { to: "/app/history", label: "History", icon: ClockCounterClockwise },
  { to: "/app/files", label: "Files", icon: FolderOpen },
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/settings", label: "Settings", icon: GearSix },
  { to: "/app/support", label: "Support", icon: Lifebuoy },
];

const PRIMARY = NAV.slice(0, 4);
const MORE = NAV.slice(4);

export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  // Close the mobile "More" menu when the route changes.
  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  // Close when clicking/tapping outside the popover.
  useEffect(() => {
    if (!moreOpen) return;
    function onPointerDown(e: PointerEvent) {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [moreOpen]);

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex min-h-screen bg-background">
      {/* Desktop sidebar — stays fixed to the viewport while content scrolls.
          `sticky top-0 self-start h-screen` pins it and prevents it from
          stretching to the height of the (taller) content. */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col self-start border-r border-outline bg-surface lg:flex">
        <div className="flex h-16 shrink-0 items-center border-b border-outline px-5">
          <Logo />
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon }, i) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary-container text-on-primary-container"
                    : "text-muted hover:bg-surface-variant hover:text-on-background"
                }`
              }
            >
              {({ isActive }) => (
                <motion.span
                  className="flex w-full items-center gap-3"
                  initial={{ x: -8, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  transition={{ delay: 0.05 * i, duration: 0.3 }}
                >
                  <Icon
                    size={20}
                    weight={isActive ? "fill" : "regular"}
                    className={isActive ? "text-on-primary-container" : ""}
                  />
                  {label}
                </motion.span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="shrink-0 border-t border-outline p-3">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-container font-display text-sm font-semibold text-on-primary-container">
              {user?.username?.slice(0, 2).toUpperCase() ?? "??"}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-on-background">{user?.username}</p>
              <p className="truncate text-xs text-muted">{user?.email}</p>
            </div>
            <button
              onClick={handleLogout}
              aria-label="Log out"
              title="Log out"
              className="text-muted transition-colors hover:scale-110 hover:text-error"
            >
              <SignOut size={20} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content — only this animates between routes */}
      <div className="flex min-w-0 flex-1 flex-col">
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {/* Animate each page in on mount. We deliberately do NOT use
              AnimatePresence mode="wait" here: when SSE job updates or the
              Queue's layout animations re-render the exiting page, the exit
              can get interrupted and mode="wait" never mounts the new page
              (blank screen). Mounting-only animation always renders. */}
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            <Outlet />
          </motion.div>
        </main>

        {/* Mobile bottom nav — primary 4 always visible; the rest live in a
            "More" popover so every destination stays reachable on small screens. */}
        <div ref={moreRef} className="sticky bottom-0 lg:hidden">
          {moreOpen && (
            <div className="absolute bottom-full right-0 left-0 border-t border-outline bg-surface shadow-lg">
              <nav className="grid grid-cols-2 gap-1 p-3" aria-label="More">
                {MORE.map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    onClick={() => setMoreOpen(false)}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium ${
                        isActive
                          ? "bg-primary-container text-on-primary-container"
                          : "text-muted hover:bg-surface-variant hover:text-on-background"
                      }`
                    }
                  >
                    <Icon size={20} />
                    {label}
                  </NavLink>
                ))}
              </nav>
            </div>
          )}
          <nav
            className="grid grid-cols-4 border-t border-outline bg-surface"
            aria-label="Mobile"
          >
            {PRIMARY.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `flex flex-col items-center gap-1 py-3 text-[11px] font-medium ${
                    isActive ? "text-primary" : "text-muted"
                  }`
                }
              >
                <Icon size={22} />
                {label}
              </NavLink>
            ))}
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              aria-expanded={moreOpen}
              aria-haspopup="menu"
              className="flex flex-col items-center gap-1 py-3 text-[11px] font-medium text-muted"
            >
              <DotsThree size={22} />
              More
            </button>
          </nav>
        </div>
      </div>
    </div>
  );
}
