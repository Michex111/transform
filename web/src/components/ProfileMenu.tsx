/**
 * The account dropdown: a circular avatar trigger and the panel behind it.
 *
 * Rendered in three places with three different rules — the desktop sidebar (the
 * identity, the account's own settings, and logout; none of the app destinations
 * its rail already lists), the phone top bar (everything, because it is the only
 * route to the account at that width) and the public header (the links and
 * logout, but no history purge). The differences are props, not forks of this
 * file, so the keyboard behaviour cannot diverge between them; each placement's
 * prop set is named in `lib/profileMenu.ts`.
 */

import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { CaretDown } from "@phosphor-icons/react";
import type { DeleteHistoryPreviewResponse } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { Avatar } from "@/components/Avatar";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import { displayNameFor } from "@/lib/avatar";
import {
  clearedHistoryMessage,
  clearHistorySummary,
  profileMenuItems,
  type ProfileMenuItem,
} from "@/lib/profileMenu";

/** The one window the menu purges. Kept as a constant so the preview, the copy and the delete agree. */
const HISTORY_RANGE = "24h" as const;

interface ProfileMenuProps {
  /** Where the panel is anchored relative to the trigger. */
  align?: "left" | "right";
  /** Whether the panel opens below or above the trigger. */
  placement?: "down" | "up";
  /**
   * Whether the panel offers logout. Every placement passes true today; the
   * default stays `false` so a new one has to opt in rather than inherit a way
   * out of the account. See `ProfileMenuOptions`. */
  showLogout?: boolean;
  /** The purge is an app-only action; the public header passes false. */
  allowHistoryDelete?: boolean;
  /**
   * Include the app destinations the desktop sidebar's rail already lists
   * (Billing, Support). Defaults to true; the sidebar passes false.
   */
  includeAppLinks?: boolean;
  /** Render the name + email beside the avatar (sidebar) or just the avatar. */
  showIdentity?: boolean;
  className?: string;
  /**
   * Start with the panel open. For tests only.
   *
   * The test environment is `node` with no DOM library, so a server render never
   * runs effects and no test can click the trigger. Without this the panel's
   * markup could only be asserted against a hand-copied fixture, which would keep
   * passing after the real panel changed.
   */
  defaultOpen?: boolean;
}

export function ProfileMenu({
  align = "right",
  placement = "down",
  showLogout = false,
  allowHistoryDelete = false,
  includeAppLinks = true,
  showIdentity = false,
  className = "",
  defaultOpen = false,
}: ProfileMenuProps) {
  const { user, logout, api } = useAuth();
  const { refresh } = useJobs();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const [open, setOpen] = useState(defaultOpen);
  const [preview, setPreview] = useState<DeleteHistoryPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLAnchorElement | HTMLButtonElement | null>>([]);
  // Guards against a deleted-account unmount landing a `setState` in the middle
  // of the preview/delete round trips below.
  //
  // The effect body RE-ASSIGNS `true`, which is load-bearing: React 18
  // StrictMode mounts, unmounts and remounts every effect in development, so a
  // cleanup-only `false` leaves this permanently false after the second mount
  // and both async handlers below would return early forever — the confirmation
  // dialog would sit on "Checking what would be removed…" and never resolve.
  // (A once-only ref combined with an unmount flag is the shape that keeps
  // breaking in this codebase; if you add single-flight here, hoist it to a
  // module instead.)
  const alive = useRef(true);

  const triggerId = useId();
  const panelId = useId();

  const items = useMemo(
    () => profileMenuItems({ showLogout, allowHistoryDelete, includeAppLinks }),
    [showLogout, allowHistoryDelete, includeAppLinks],
  );
  const name = displayNameFor(user);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const focusTrigger = useCallback(() => triggerRef.current?.focus(), []);

  // Real DOM focus on the item node, wrapping at both ends — not
  // `aria-activedescendant`, so a screen reader announces each entry as it is
  // reached (same algorithm as `Dropdown.tsx`).
  const focusItem = useCallback(
    (index: number) => {
      const count = items.length;
      if (count === 0) return;
      const next = (index + count) % count;
      setActiveIndex(next);
      itemRefs.current[next]?.focus();
    },
    [items.length],
  );

  // Opening always lands on the first item. Enter/Space arrive here through the
  // button's own click, so they need no separate path; ArrowDown is handled on
  // the trigger and then rides this same effect, because the item node does not
  // exist yet in the tick that opens the panel.
  useEffect(() => {
    if (open) focusItem(0);
  }, [open, focusItem]);

  // Close when the route changes — either a link was followed from the panel, or
  // something else navigated while it was open.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Escape closes and hands focus back to the trigger. Focus rests on a menu
  // item, which unmounts with the panel, so without this the next Tab would
  // restart at the top of the document.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        focusTrigger();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, focusTrigger]);

  // Close on a press outside the trigger + panel.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function handleTriggerKeyDown(e: React.KeyboardEvent) {
    // ArrowDown opens the menu onto its first item; Enter/Space already click.
    if (e.key !== "ArrowDown") return;
    e.preventDefault();
    setOpen(true);
  }

  function handlePanelKeyDown(e: React.KeyboardEvent) {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusItem(activeIndex + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusItem(activeIndex - 1);
        break;
      case "Home":
        e.preventDefault();
        focusItem(0);
        break;
      case "End":
        e.preventDefault();
        focusItem(items.length - 1);
        break;
      case "Tab":
        // Natural close — the browser moves focus on, the panel gets out of the way.
        setOpen(false);
        break;
      default:
        // Enter and Space are deliberately NOT handled: the focused entry is a
        // real <a>/<button>, so activation is the browser's job. Intercepting
        // them here would fire the action twice.
        break;
    }
  }

  /**
   * Count first, confirm second.
   *
   * The modal opens immediately and shows a loading line, because the count is
   * the whole point of the confirmation: a placeholder number would be a number
   * the user could agree to. The same reasoning closes the modal if the count
   * cannot be fetched.
   */
  async function openClearHistoryConfirm() {
    setPreview(null);
    setPreviewLoading(true);
    setConfirmOpen(true);
    try {
      const result = await api.deleteHistoryPreview(HISTORY_RANGE);
      if (!alive.current) return;
      setPreview(result);
    } catch {
      if (!alive.current) return;
      setConfirmOpen(false);
      error("Could not check your recent history. Try again in a moment.");
    } finally {
      if (alive.current) setPreviewLoading(false);
    }
  }

  async function confirmClearHistory() {
    setConfirming(true);
    try {
      const result = await api.deleteHistoryRange(HISTORY_RANGE);
      if (!alive.current) return;
      setConfirmOpen(false);
      success(clearedHistoryMessage(result));
      // Reconcile the queue and history with the server *after* reporting
      // success: `refresh` never throws, but a slow one must not hold the modal
      // open over an action that has already happened.
      await refresh();
    } catch {
      if (!alive.current) return;
      // Leave the modal open. The count is still accurate and the entries are
      // still there, so retrying is one click rather than a reopened menu.
      error("Could not delete your history. Try again in a moment.");
    } finally {
      if (alive.current) setConfirming(false);
    }
  }

  function handleSelect(item: ProfileMenuItem) {
    setOpen(false);
    if (item.id === "clear-history") {
      void openClearHistoryConfirm();
      return;
    }
    if (item.id === "logout") {
      logout();
      navigate("/login", { replace: true });
    }
  }

  const reduce = useReducedMotion();
  const slide = placement === "up" ? 6 : -6;
  const panelPosition = `absolute z-50 min-w-[15rem] max-w-[min(20rem,calc(100vw-1.5rem))] overflow-hidden rounded-lg border border-outline bg-surface p-1 shadow-xl ${
    placement === "up" ? "bottom-full mb-2" : "top-full mt-2"
  } ${align === "right" ? "right-0" : "left-0"}`;

  const itemClass = (item: ProfileMenuItem) =>
    `flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors pointer-coarse:min-h-11 ${
      item.destructive
        ? "text-error hover:bg-error/10 focus:bg-error/10"
        : "text-on-background hover:bg-surface-variant hover:text-on-background focus:bg-surface-variant"
    }`;

  return (
    <>
      <div ref={rootRef} className={`relative inline-block ${className}`}>
        <button
          ref={triggerRef}
          id={triggerId}
          type="button"
          onClick={() => setOpen((v) => !v)}
          onKeyDown={handleTriggerKeyDown}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`Account menu for ${name}`}
          className={
            showIdentity
              ? // Same paddings and avatar edge as the static footer row this
                // replaced, so swapping it in does not shift the sidebar.
                "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-surface-variant pointer-coarse:min-h-11"
              : // A bare 32px tile is below the 44px touch minimum, so the hit
                // area is padded out on coarse pointers only.
                "flex shrink-0 items-center justify-center rounded-full p-1 transition-colors hover:bg-surface-variant pointer-coarse:min-h-11 pointer-coarse:min-w-11"
          }
        >
          <Avatar user={user} size={showIdentity ? 36 : 32} />
          {showIdentity && (
            <>
              {/* <span>s, not <p>s: a button's content model is phrasing content,
                  so the name/email pair has to be inline elements set to block. */}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-on-background">{name}</span>
                <span className="block truncate text-xs text-muted">{user?.email}</span>
              </span>
              <CaretDown
                size={14}
                aria-hidden="true"
                className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
              />
            </>
          )}
        </button>

        <AnimatePresence>
          {open && (
            <motion.div
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: slide }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, y: slide }}
              transition={{ duration: reduce ? 0 : 0.16, ease: [0.22, 1, 0.36, 1] }}
              className={panelPosition}
            >
              {/* Non-interactive identity block — not focusable, so the roving
                  arrow keys go straight to the entries.

                  It sits *outside* the `role="menu"` element on purpose: a menu
                  may only own menuitems (plus the separators between them), so a
                  header inside it is an ARIA violation — audited as a menu with
                  a child that is neither. Visually nothing moves, because both
                  are children of the same padded, animated panel.

                  Omitted when `showIdentity` is set: the sidebar trigger is
                  itself the name + email + avatar, so repeating the trio one
                  row above the entries made the panel look like it had failed
                  to render anything new. */}
              {!showIdentity && (
                <>
                  <div className="flex items-center gap-3 px-3 py-2.5">
                    <Avatar user={user} size={40} />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-on-background">{name}</p>
                      <p className="truncate text-xs text-muted">{user?.email}</p>
                    </div>
                  </div>
                  <div role="separator" className="my-1 h-px bg-outline" />
                </>
              )}

              {/* aria-controls lives on the trigger (that is the pair that means
                  anything); the menu is named by the trigger, whose label already
                  says which account it belongs to. */}
              <div
                id={panelId}
                role="menu"
                aria-labelledby={triggerId}
                onKeyDown={handlePanelKeyDown}
              >
                {items.map((item, index) => {
                  const Icon = item.icon;
                  // A `role="separator"` is one of the few things a menu may
                  // contain besides its items, so the rule is drawn as its own
                  // element instead of wrapping each entry in a generic <div>.
                  return (
                    <Fragment key={item.id}>
                      {item.dividerBefore && (
                        <div role="separator" className="my-1 h-px bg-outline" />
                      )}
                      {item.to ? (
                        <Link
                          ref={(node) => {
                            itemRefs.current[index] = node;
                          }}
                          to={item.to}
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => setOpen(false)}
                          className={itemClass(item)}
                        >
                          <Icon size={18} className="shrink-0" />
                          {item.label}
                        </Link>
                      ) : (
                        <button
                          ref={(node) => {
                            itemRefs.current[index] = node;
                          }}
                          type="button"
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => handleSelect(item)}
                          className={itemClass(item)}
                        >
                          <Icon size={18} className="shrink-0" />
                          {item.label}
                        </button>
                      )}
                    </Fragment>
                  );
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Outside the popover's box so the absolute panel's layout is unaffected. */}
      <Modal
        open={confirmOpen}
        onClose={() => {
          // Escape/backdrop are refused mid-delete: the request is already in
          // flight and the outcome has to be reported.
          if (!confirming) setConfirmOpen(false);
        }}
        title="Delete recent history"
        description="Only conversions from the last 24 hours are affected."
      >
        {previewLoading || !preview ? (
          <div className="space-y-3" aria-busy="true">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <p className="text-xs text-muted">Checking what would be removed…</p>
          </div>
        ) : (
          <p className="text-sm text-on-background">{clearHistorySummary(preview)}</p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setConfirmOpen(false)}
            disabled={confirming}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => void confirmClearHistory()}
            disabled={confirming || previewLoading || !preview || preview.count === 0}
          >
            {confirming ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </Modal>
    </>
  );
}
