import { useEffect, useRef, type ReactNode } from "react";
import { X } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";

/** Everything inside a panel a keyboard user can reach with Tab. */
const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** The focusable descendants of `root`, in document order, minus disabled ones. */
function focusableWithin(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute("disabled"),
  );
}

/** An accessible modal dialog: backdrop, focus-on-open, Escape to close,
 *  `role="dialog"` + `aria-modal`, and a scrollable body. */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  maxWidth = "max-w-md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  maxWidth?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const prevFocus = useRef<HTMLElement | null>(null);
  // The latest `onClose` in a ref, so the effect below can depend on `open`
  // alone. Callers pass a fresh closure on every render (they have to: closing
  // must see the current `busy` flag), and an effect keyed on it re-runs on
  // every keystroke inside the dialog — each re-run restores focus to the
  // element that opened the dialog before moving it back in, which yanks focus
  // out of the field being typed into. The effect is about the dialog's
  // lifetime, not about the identity of a callback.
  const onCloseRef = useRef(onClose);
  // Assigned in an effect rather than during render: a render-time write to a
  // ref is not safe when a render is discarded (concurrent rendering), and this
  // costs one no-op effect run per render.
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // Focus management + Escape to close + focus trap.
  useEffect(() => {
    if (!open) return;
    prevFocus.current = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => {
      // Initial focus goes to the first control in the BODY, falling back to
      // the panel when the body has none. Focusing the container makes a
      // keyboard user Tab through the dialog's chrome (the close button) to
      // reach the field they were asked to fill in, and `autoFocus` on a child
      // cannot be relied on because this timer runs after it and would move
      // focus back out again — which is why `DangerZoneSection`'s delete dialog
      // opens with its password box unfocused. The panel is still the fallback,
      // so a dialog with no controls at all receives focus (and therefore still
      // traps it).
      const target = focusableWithin(bodyRef.current)[0] ?? panelRef.current;
      target?.focus();
    }, 30);

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = focusableWithin(panelRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      // If focus somehow left the panel, pull it back to the first element.
      if (active && !panelRef.current?.contains(active)) {
        e.preventDefault();
        first.focus();
        return;
      }

      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      prevFocus.current?.focus?.();
    };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <motion.div
            className="absolute inset-0 bg-black/60"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.div
            ref={panelRef}
            tabIndex={-1}
            role="document"
            className={`relative flex w-full ${maxWidth} max-h-[calc(100dvh-1.5rem)] flex-col overflow-hidden rounded-2xl border border-outline bg-surface shadow-2xl focus:outline-none sm:max-h-[calc(100dvh-2rem)]`}
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.97 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="flex shrink-0 items-start justify-between gap-3 border-b border-outline px-5 py-4">
              <div className="min-w-0">
                <h2 className="font-display text-lg font-semibold text-on-background">{title}</h2>
                {description && <p className="mt-0.5 truncate text-sm text-muted">{description}</p>}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="-m-1.5 shrink-0 rounded-md p-3 text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-fine:m-0 pointer-fine:p-1.5"
              >
                <X size={18} />
              </button>
            </div>
            {/* `min-h-0` is required for a flex child to shrink below its
                content height, which is what lets this scroll instead of pushing
                the panel past the viewport on a short screen. `bodyRef` is the
                scope for initial focus: the first control a caller renders is
                the one they expect the user to start in. */}
            <div
              ref={bodyRef}
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:p-5"
            >
              {children}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
