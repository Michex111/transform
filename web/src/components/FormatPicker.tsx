import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { motion } from "motion/react";
import { MagnifyingGlass, CaretDown, CaretRight, Check } from "@phosphor-icons/react";
import { FORMAT_CATEGORIES } from "@/lib/formatCatalog";
import { formatTint, formatVisual } from "@/lib/formatVisual";
import { isPickableFormat, restrictFormatCategories } from "@/lib/formatPickerOptions";
import { useMediaQuery } from "@/lib/useMediaQuery";

/**
 * True when the viewport is too small for the anchored popover: phone width, or
 * too short for the panel to hang below the trigger (a landscape phone measures
 * 740x360). `useMediaQuery` subscribes to changes, so rotating the device
 * re-lays it out.
 */
function useCompactViewport(): boolean {
  return useMediaQuery("(max-width: 639px), (max-height: 32rem)");
}

/**
 * Renders its children into `document.body` when `enabled`.
 *
 * A non-`none` CSS `transform` on ANY ancestor makes that ancestor the
 * containing block for `position: fixed` descendants, so an overlay inside a
 * page-level motion wrapper is centred within that wrapper instead of the
 * viewport — verified in production, where the wrapper resolved to
 * [16, 34, 343x855] rather than the 390x844 viewport and the panel sat ~47px
 * below centre. Portalling to the body removes the dependency entirely.
 *
 * Falls back to rendering inline when there is no DOM (server rendering).
 */
function PopoverPortal({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  if (!enabled || typeof document === "undefined") return <>{children}</>;
  return createPortal(children, document.body);
}

interface FormatPickerProps {
  /** Currently selected format extension, e.g. "pdf". */
  value: string;
  /** Called when the user picks a format. */
  onChange: (ext: string) => void;
  /** Categories to show, or all if omitted. */
  categories?: typeof FORMAT_CATEGORIES;
  /** Optional label/aria for the trigger. */
  ariaLabel?: string;
  align?: "left" | "right";
  /**
   * Valid extension extensions for this picker. When provided, ONLY these may
   * be picked. An empty list means there is nothing valid to offer yet (the
   * backend graph has not arrived), so the picker offers nothing rather than
   * falling back to the entire static catalogue — that fallback used to let
   * users select pairs the server rejects. Omit to leave the picker
   * unrestricted.
   */
  allowed?: string[];
  /** True while `allowed` is still being resolved, for a friendlier empty state. */
  pending?: boolean;
}

/** A rich format picker — a popover with a category sidebar, a search box,
 *  and a scrollable grid of format buttons. Matches the reference design. */
export function FormatPicker({
  value,
  onChange,
  categories = FORMAT_CATEGORIES,
  ariaLabel = "Choose format",
  align = "left",
  allowed,
  pending = false,
}: FormatPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeCat, setActiveCat] = useState(categories[0]?.id ?? "");
  const rootRef = useRef<HTMLDivElement>(null);
  // Also covers the portalled overlay, which is NOT inside `rootRef` — without
  // this, clicking inside the panel on a phone would count as an outside click
  // and close the picker.
  const overlayRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Restrict visible categories to those containing an allowed format. An
  // `undefined` allowed list leaves the picker unrestricted; an explicitly empty
  // one restricts it to nothing, so the popover offers no formats at all.
  const restrictedCategories = useMemo(
    () => restrictFormatCategories(allowed, categories),
    [allowed, categories],
  );

  // Whether the picker has anything it could legitimately offer.
  const hasOptions = restrictedCategories.length > 0;

  // Close when clicking outside, or on Escape (it is a dismissible overlay on
  // phones, so the keyboard path has to work as well as the pointer one).
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (overlayRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Focus the search box when opening.
  useEffect(() => {
    if (open) {
      setQuery("");
      window.setTimeout(() => searchRef.current?.focus(), 50);
    }
  }, [open]);

  const activeCategory =
    restrictedCategories.find((c) => c.id === activeCat) ?? restrictedCategories[0];

  const visibleFormats = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return activeCategory?.formats ?? [];
    return restrictedCategories.flatMap((c) =>
      c.formats.filter((f) => f.ext.toLowerCase().includes(q) || f.label.toLowerCase().includes(q)),
    );
  }, [query, activeCategory, restrictedCategories]);

  const selected = getSelectedLabel(value);

  // If the currently selected value is no longer allowed, disable the trigger.
  const isAllowed = isPickableFormat(allowed, value);

  // A phone-width viewport cannot fit the anchored popover, and neither can a
  // SHORT one — a landscape phone is wide (>=640px), so a width-only breakpoint
  // would hand it the anchored panel and run it off the bottom of the screen.
  // Width OR height is used rather than `pointer: coarse`, which cannot express
  // "this window is too small for a 520px panel".
  const compact = useCompactViewport();

  function pick(ext: string) {
    onChange(ext);
    setOpen(false);
  }

  return (
    <div ref={rootRef} className="relative">
      {/* Trigger card */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-disabled={!isAllowed}
        aria-busy={pending}
        title={
          !isAllowed
            ? pending
              ? "Loading the supported formats…"
              : "No supported format is available for this conversion."
            : undefined
        }
        className={`flex w-28 flex-col items-center gap-2 rounded-xl border border-outline bg-surface px-3 py-4 text-sm font-semibold text-on-background transition-colors hover:border-primary/50 focus:border-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${
          isAllowed ? "" : "cursor-not-allowed opacity-50"
        }`}
      >
        <FormatIcon ext={value} />
        <span className="uppercase">{selected}</span>
        <CaretDown size={14} className="text-muted" />
      </button>

      {/* NOTE: deliberately NOT wrapped in `AnimatePresence`. Its direct child
          here is `PopoverPortal`, and AnimatePresence can only track motion
          children — it kept the portal mounted forever, so picking a format,
          clicking the backdrop and pressing Escape all left the panel open.
          Gating on `open` keeps every close path working; the panel keeps its
          enter animation and loses only the 160ms fade on close. */}
      {open && (
          /* An anchored popover cannot be relied on for a phone: the panel is
             520px wide and the trigger sits near the screen edge, so at 390px it
             used to hang off the viewport (measured: left = -10px), and on a
             LANDSCAPE phone (740x360) it ran off the bottom of the screen. When
             the viewport is phone-width OR too short for the panel, it becomes a
             vertically-centred overlay with a backdrop instead.

             The wrapper is `display: contents` in the anchored case, so its box
             disappears and the panel positions against the trigger's wrapper
             exactly as it always did — the desktop popover is unchanged. The
             overlay is portalled to the body so no transformed ancestor can
             contain it (see `PopoverPortal`). */
          <PopoverPortal enabled={compact}>
          <div
            ref={overlayRef}
            className={
              compact
                ? "fixed inset-0 z-50 flex items-center justify-center p-3"
                : "contents"
            }
          >
            {/* Dismiss target outside the panel (overlay only). */}
            <div
              className={compact ? "absolute inset-0 bg-black/60" : "hidden"}
              onClick={() => setOpen(false)}
              aria-hidden
            />
            <motion.div
              initial={{ opacity: 0, y: 6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 6, scale: 0.98 }}
              transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
              // In the overlay case `relative` keeps the panel above the
              // backdrop and the bounded height keeps it fully on screen (its
              // body scrolls); otherwise it stays anchored under the trigger.
              className={`flex overflow-hidden rounded-xl border border-outline bg-surface shadow-xl ${
                compact
                  ? "relative max-h-[calc(100dvh-1.5rem)] w-full max-w-[520px]"
                  : `absolute z-50 mt-2 max-h-[75vh] w-[520px] max-w-[90vw] ${
                      align === "right" ? "right-0" : "left-0"
                    }`
              }`}
            >
            {!hasOptions ? (
              // Nothing may legitimately be picked (the conversion graph has not
              // arrived, or this format converts to nothing). Showing the full
              // catalogue here would offer formats the server would reject, so
              // say why the list is empty instead.
              <p className="flex-1 p-8 text-center text-sm text-muted" role="status">
                {pending ? "Loading supported formats…" : "No supported formats available."}
              </p>
            ) : (
              <>
                {/* Category sidebar */}
                <div className="flex min-h-0 w-40 shrink-0 flex-col border-r border-outline">
                  <div className="border-b border-outline p-2">
                    <div className="flex items-center gap-2 rounded-lg bg-surface-variant px-2 py-1.5">
                      <MagnifyingGlass size={14} className="text-muted" />
                      <input
                        ref={searchRef}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search Format"
                        className="w-full bg-transparent text-sm text-on-background placeholder:text-muted focus:outline-none"
                        aria-label="Search format"
                      />
                    </div>
                  </div>
                  <nav className="flex-1 overflow-y-auto py-1" aria-label="Format categories">
                    {restrictedCategories.map((cat) => (
                      <button
                        key={cat.id}
                        onClick={() => {
                          setActiveCat(cat.id);
                          setQuery("");
                        }}
                        className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors ${
                          cat.id === activeCategory?.id && !query
                            ? "bg-primary-container text-on-primary-container"
                            : "text-muted hover:bg-surface-variant hover:text-on-background"
                        }`}
                      >
                        {cat.name}
                        <CaretRight size={12} className="opacity-60" />
                      </button>
                    ))}
                  </nav>
                </div>

                {/* Format grid. These are toggle buttons, not a listbox: the
                    popover is a 3-column visual grid (with a category sidebar
                    and a search box) rather than a linear list, so the buttons
                    expose their state with `aria-pressed` and the trigger
                    makes no `aria-haspopup` claim. */}
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  <div className="grid grid-cols-3 gap-1.5">
                    {visibleFormats.map((f) => {
                      const isSel = f.ext === value;
                      return (
                        <button
                          key={f.ext}
                          onClick={() => pick(f.ext)}
                          aria-pressed={isSel}
                          className={`relative flex h-9 items-center justify-center rounded border font-mono text-xs font-semibold transition-colors ${
                            isSel
                              ? "border-primary bg-primary-container text-on-primary-container"
                              : "border-outline bg-surface-variant/40 text-on-background hover:border-primary/50"
                          }`}
                        >
                          {f.label}
                          {isSel && (
                            <span className="absolute right-1 top-1 text-primary">
                              <Check size={10} weight="bold" />
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  {visibleFormats.length === 0 && (
                    <p className="py-8 text-center text-sm text-muted">No formats found.</p>
                  )}
                </div>
              </>
            )}
            </motion.div>
          </div>
          </PopoverPortal>
      )}
    </div>
  );
}

/** A small colored tile icon representing a format (like the reference cards). */
export function FormatIcon({ ext }: { ext: string }) {
  const { color } = formatVisual(ext);
  return (
    <span
      className="flex h-10 w-10 items-center justify-center rounded-lg"
      // `formatTint` (color-mix): appending an alpha hex suffix to a
      // `var(--token)` colour (`${color}1a`) is invalid CSS and is dropped, so
      // the tile lost its tinted fill and border.
      style={{
        backgroundColor: formatTint(color, 10),
        color,
        border: `1px solid ${formatTint(color, 25)}`,
      }}
      aria-hidden
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
      </svg>
    </span>
  );
}

function getSelectedLabel(ext: string): string {
  // The canonical, case-insensitive format lookup — one source of truth for a
  // format's label and colour.
  return formatVisual(ext).label;
}
