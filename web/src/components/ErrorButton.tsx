import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Warning } from "@phosphor-icons/react";

/** A small "show error" button for a failed conversion. Clicking it reveals
 *  the record's error message in an animated popover.
 *
 *  The popover is rendered through a portal to <body> and positioned at the
 *  button's screen coordinates so it is never clipped by an ancestor with
 *  `overflow-hidden` (e.g. the Card around each record table/list). */
export function ErrorButton({ message }: { message?: string }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);

  // Measure the button's position so the popover can be placed precisely.
  useEffect(() => {
    if (!open) return;
    function measure() {
      const rect = rootRef.current?.getBoundingClientRect();
      if (rect) setPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
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

  if (!message) {
    return (
      <span
        className="text-muted"
        aria-label="No error message"
        title="No error message"
      >
        <Warning size={18} />
      </span>
    );
  }

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Show error message"
        aria-expanded={open}
        title="Error details"
        className="text-error transition-transform hover:scale-110"
      >
        <Warning size={18} weight="fill" />
      </button>

      {open &&
        createPortal(
          <AnimatePresence>
            <motion.div
              initial={{ opacity: 0, y: 6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 6, scale: 0.98 }}
              transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
              style={pos ? { top: pos.top, right: pos.right, position: "fixed" } : { position: "fixed" }}
              className="z-50 w-72 rounded-lg border border-error/30 bg-surface p-3 shadow-xl"
            >
              <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-error">
                <Warning size={14} weight="fill" /> Error
              </p>
              <p className="text-sm text-on-background">{message}</p>
            </motion.div>
          </AnimatePresence>,
          document.body,
        )}
    </div>
  );
}
