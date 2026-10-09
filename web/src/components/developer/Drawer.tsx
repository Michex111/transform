import { X } from "@phosphor-icons/react";
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A right-hand details drawer.
 *
 * Portalled to `document.body` deliberately: the app's page wrapper carries a
 * motion `transform`, and a non-`none` transform on *any* ancestor makes that
 * ancestor the containing block for `position: fixed` descendants — so an
 * un-portalled drawer would be positioned relative to the page instead of the
 * viewport. (The same trap the `Modal` and `UploadsDock` document; it produced a
 * fixed overlay measured 47px off-centre before.)
 *
 * Keyboard contract: Escape closes, focus moves into the panel on open, Tab is
 * trapped, and focus returns to the element that opened it on close — so a
 * keyboard user cannot end up behind the overlay.
 */
export function Drawer({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    // Focus the panel (not its first control) so a long detail view starts at
    // the top; the panel is focusable via `tabIndex={-1}`.
    panelRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button
        type="button"
        aria-label="Close details"
        className="absolute inset-0 h-full w-full cursor-default bg-overlay"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : "Details"}
        className="relative flex h-full w-full max-w-md flex-col border-l border-outline bg-surface shadow-[var(--shadow-e4)] outline-none"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-outline px-5 py-4">
          <div className="min-w-0">{title}</div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-m-2 shrink-0 rounded-lg p-2 text-muted transition-colors hover:bg-surface-variant hover:text-on-background focus-visible:ring-2 focus-visible:ring-primary"
          >
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <footer className="shrink-0 border-t border-outline px-5 py-3">{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  );
}
