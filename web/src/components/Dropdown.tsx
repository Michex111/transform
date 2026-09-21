import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CaretDown, Check } from "@phosphor-icons/react";

export interface DropdownOption<T extends string = string> {
  value: T;
  label: string;
  /** Optional leading icon glyph (used by some dropdowns). */
  icon?: React.ReactNode;
}

interface DropdownProps<T extends string = string> {
  value: T;
  onChange: (value: T) => void;
  options: DropdownOption<T>[];
  /** Small helper text prefix inside the trigger, e.g. "Sort". */
  label?: string;
  ariaLabel?: string;
  align?: "left" | "right";
  className?: string;
  disabled?: boolean;
}

/** A modern, app-themed dropdown (custom popover with animated open/close,
 *  full keyboard support, and a check on the selected item). Replaces native
 *  `<select>` everywhere for visual consistency. */
export function Dropdown<T extends string = string>({
  value,
  onChange,
  options,
  label,
  ariaLabel,
  align = "left",
  className = "",
  disabled = false,
}: DropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const selected = options.find((o) => o.value === value);
  const selectedIndex = options.findIndex((o) => o.value === value);

  // Move real DOM focus onto the option button at `activeIndex`. Prefer real
  // focus so screen readers announce each option as it is reached.
  function focusOption(index: number) {
    const items = listRef.current?.querySelectorAll<HTMLButtonElement>("button[role='option']");
    if (!items || items.length === 0) return;
    const target = items[(index + items.length) % items.length];
    setActiveIndex((index + items.length) % items.length);
    target?.focus();
  }

  // Focus lives on an option while the popup is open, and the popup unmounts on
  // close — so focus has to go back to the trigger (otherwise it falls to
  // `<body>` and the next Tab restarts at the top of the document).
  function focusTrigger() {
    rootRef.current?.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.focus();
  }

  // On open, move focus to the selected option (or the first option).
  useEffect(() => {
    if (!open) return;
    const start = selectedIndex >= 0 ? selectedIndex : 0;
    focusOption(start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      if (e.key === "Escape") {
        setOpen(false);
        focusTrigger();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const count = options.length;

  function handleListKeyDown(e: React.KeyboardEvent) {
    if (!open) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusOption(activeIndex + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusOption(activeIndex - 1);
        break;
      case "Home":
        e.preventDefault();
        focusOption(0);
        break;
      case "End":
        e.preventDefault();
        focusOption(count - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (activeIndex >= 0 && options[activeIndex]) {
          onChange(options[activeIndex].value);
        }
        setOpen(false);
        focusTrigger();
        break;
      case "Tab":
        // Natural close — let the browser move focus away.
        setOpen(false);
        break;
      default:
        break;
    }
  }

  return (
    <div ref={rootRef} className={`relative inline-block ${className}`}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-activedescendant={
          open && activeIndex >= 0 && options[activeIndex]
            ? `${ariaLabel ?? "dropdown"}-opt-${options[activeIndex].value}`
            : undefined
        }
        disabled={disabled}
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-outline-strong bg-surface-variant px-3 text-sm text-on-background transition-colors hover:border-primary/60 focus:border-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
      >
        {label && <span className="font-medium text-muted">{label}</span>}
        <span className="font-medium">{selected?.label ?? value}</span>
        <CaretDown
          size={14}
          className={`text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.ul
            ref={listRef}
            role="listbox"
            onKeyDown={handleListKeyDown}
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className={`absolute z-50 mt-2 min-w-[12rem] overflow-hidden rounded-lg border border-outline bg-surface p-1 shadow-xl ${
              align === "right" ? "right-0" : "left-0"
            }`}
          >
            {options.map((opt) => {
              const isSel = opt.value === value;
              return (
                <li key={opt.value}>
                  <button
                    type="button"
                    id={`${ariaLabel ?? "dropdown"}-opt-${opt.value}`}
                    role="option"
                    aria-selected={isSel}
                    onClick={() => {
                      onChange(opt.value);
                      setOpen(false);
                      // The clicked option unmounts with the popup, so keep the
                      // keyboard user's place on the trigger.
                      focusTrigger();
                    }}
                    className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                      isSel
                        ? "bg-primary-container text-on-primary-container"
                        : "text-on-background hover:bg-surface-variant"
                    }`}
                  >
                    {opt.icon && <span className="shrink-0">{opt.icon}</span>}
                    <span className="flex-1">{opt.label}</span>
                    {isSel && <Check size={14} weight="bold" className="shrink-0" />}
                  </button>
                </li>
              );
            })}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}
