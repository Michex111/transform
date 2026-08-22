import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { MagnifyingGlass, CaretDown, CaretRight, Check } from "@phosphor-icons/react";
import { FORMAT_CATEGORIES, type FormatDef } from "@/lib/formatCatalog";

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
  /** If provided, only these extensions may be picked (valid target formats). */
  allowed?: string[];
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
}: FormatPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeCat, setActiveCat] = useState(categories[0]?.id ?? "");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Restrict visible categories to those containing an allowed format.
  const allowedSet = useMemo(() => new Set((allowed ?? []).map((a) => a.toLowerCase())), [allowed]);
  const restrictedCategories = useMemo(() => {
    if (!allowedSet.size) return categories;
    return categories
      .map((cat) => ({ ...cat, formats: cat.formats.filter((f) => allowedSet.has(f.ext)) }))
      .filter((cat) => cat.formats.length > 0);
  }, [categories, allowedSet]);

  // Close when clicking outside.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
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
  const isAllowed = !allowedSet.size || allowedSet.has(value.toLowerCase());

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
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-disabled={!isAllowed}
        className="flex w-28 flex-col items-center gap-2 rounded-xl border border-outline bg-surface px-3 py-4 text-sm font-semibold text-on-background transition-colors hover:border-primary/50 focus:border-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
      >
        <FormatIcon ext={value} />
        <span className="uppercase">{selected}</span>
        <CaretDown size={14} className="text-muted" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className={`absolute z-50 mt-2 flex w-[520px] max-w-[90vw] overflow-hidden rounded-xl border border-outline bg-surface shadow-xl ${
              align === "right" ? "right-0" : "left-0"
            }`}
          >
            {/* Category sidebar */}
            <div className="flex w-40 shrink-0 flex-col border-r border-outline">
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

            {/* Format grid */}
            <div className="flex-1 overflow-y-auto p-3">
              <div className="grid grid-cols-3 gap-1.5">
                {visibleFormats.map((f) => {
                  const isSel = f.ext === value;
                  return (
                    <button
                      key={f.ext}
                      onClick={() => pick(f.ext)}
                      aria-selected={isSel}
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
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** A small colored tile icon representing a format (like the reference cards). */
export function FormatIcon({ ext }: { ext: string }) {
  const def = getFormatDefForIcon(ext);
  const color = def?.color ?? "var(--color-fmt-text)";
  return (
    <span
      className="flex h-10 w-10 items-center justify-center rounded-lg"
      style={{ backgroundColor: `${color}1a`, color, border: `1px solid ${color}40` }}
      aria-hidden
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
      </svg>
    </span>
  );
}

function getFormatDefForIcon(ext: string): FormatDef | undefined {
  // Avoid importing the catalog twice — find by walking the same data.
  return FORMAT_CATEGORIES.flatMap((c) => c.formats).find((f) => f.ext === ext);
}

function getSelectedLabel(ext: string): string {
  return getFormatDefForIcon(ext)?.label ?? ext.toUpperCase();
}
