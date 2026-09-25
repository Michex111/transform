import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DotsThreeVertical } from "@phosphor-icons/react";
import type { ReactNode } from "react";

export interface RowMenuItem {
  /** Stable identity, used as the React key. */
  key: string;
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  /** Renders the item in the error colour (destructive or terminal actions). */
  danger?: boolean;
  disabled?: boolean;
  /**
   * Free text shown above the items, for context the labels cannot carry — the
   * failure message of a failed conversion, for instance, which otherwise needs
   * a second tap on a nested popover to read on a phone.
   */
  detail?: string;
}

/**
 * The ⋮ "more options" menu for a data-row, on the layout where the row cannot
 * show its actions inline.
 *
 * WHY IT IS PORTALLED
 * The menu is rendered into `document.body` and positioned from the trigger's
 * measured rect. Inside the row it would be clipped by the card's
 * `overflow-hidden` and trapped in a Framer Motion stacking context (the rows
 * animate in with a transform), so a later row would paint over it.
 *
 * WHY IT IS A SEPARATE COMPONENT FROM `Dropdown`
 * `Dropdown` is a value picker — it takes a `value`/`onChange` and renders the
 * selection. This is an action menu: no value, items that fire and close. The
 * two look similar and behave differently, and folding them together would mean
 * a prop soup of mutually exclusive modes.
 */
export function RowMenu({
  label,
  items,
  disabled,
}: {
  /** Accessible name for the trigger, e.g. "More options for report.pdf". */
  label: string;
  items: RowMenuItem[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });

  // Measure the trigger so the portalled menu lands under it, clamped to the
  // viewport, and flipping above when there is no room below.
  useLayoutEffect(() => {
    if (!open) return;
    function measure() {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      // w-52 = 13rem = 208px, plus the two 4px borders.
      const menuWidth = 208 + 8;
      const margin = 8;
      const right = Math.max(margin, window.innerWidth - rect.right - menuWidth);

      const menuHeight = menuRef.current?.offsetHeight ?? 180;
      const spaceBelow = window.innerHeight - rect.bottom - margin;
      const preferred =
        menuHeight <= spaceBelow
          ? Math.max(margin, rect.bottom + margin)
          : Math.max(margin, rect.top - menuHeight - margin);
      // Clamped as well as flipped: the first measurement can happen before the
      // menu's text has wrapped, so a height that grows afterwards would push the
      // bottom edge past the viewport (measured: a 4px overshoot at 390x844 on the
      // row nearest the bottom). The clamp keeps the menu fully on screen
      // whatever the height turns out to be.
      const top = Math.min(preferred, Math.max(margin, window.innerHeight - menuHeight - margin));
      setPos({ top, right });
    }
    measure();
    // Second pass on the next frame: `offsetHeight` read in the same commit as
    // the portal can predate the menu's real height.
    const frame = window.requestAnimationFrame(measure);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  // Focus the first item on open; hand focus back to the trigger on close, so a
  // keyboard user is returned to where they were rather than to <body>.
  useEffect(() => {
    if (!open) return;
    // Captured, not read in the cleanup: by then `triggerRef.current` may point
    // at a different node (or none), and the effect means to restore focus to
    // the trigger this cycle belonged to.
    const trigger = triggerRef.current;
    const first = menuRef.current?.querySelector<HTMLElement>("button");
    first?.focus();
    return () => trigger?.focus();
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative" draggable={false}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-outline-strong text-muted transition-colors hover:bg-surface-variant hover:text-on-background disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
      >
        <DotsThreeVertical size={18} weight="bold" />
      </button>
      {open &&
        createPortal(
          <>
            {/* A full-viewport catcher, so a tap anywhere else closes the menu
                (and so the row underneath is not clickable while it is open). */}
            <div className="fixed inset-0 z-[65]" onClick={() => setOpen(false)} />
            <div
              ref={menuRef}
              role="menu"
              aria-label={label}
              style={{ position: "fixed", top: pos.top, right: pos.right }}
              className="z-[70] w-52 overflow-hidden rounded-lg border border-outline bg-surface p-1 shadow-xl"
            >
              {items.map((item) => (
                <div key={item.key}>
                  {item.detail && (
                    // Wraps and scrolls rather than truncating: a failure whose
                    // reason is cut off is worse than no reason at all.
                    <p className="max-h-24 overflow-y-auto px-3 pb-1 pt-2 text-xs text-muted">
                      {item.detail}
                    </p>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={() => {
                      setOpen(false);
                      item.onSelect();
                    }}
                    className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      item.danger
                        ? "text-error hover:bg-error/10"
                        : "text-on-background hover:bg-surface-variant"
                    }`}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                </div>
              ))}
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}
