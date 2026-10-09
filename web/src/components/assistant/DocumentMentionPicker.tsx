import { CircleNotch, FileMagnifyingGlass, WarningCircle } from "@phosphor-icons/react";
import type { FileMetadataResponse } from "@/api/types";
import { FormatThumb } from "@/components/FormatThumb";
import { formatBytes, formatDateOrNull } from "@/lib/format";
import { attachmentExtension } from "@/lib/assistantAttachments";

/**
 * The `@` document picker's popup.
 *
 * Presentational on purpose: the search, the debounce and the highlight all live
 * in `useDocumentMention`, and every key is handled by the composer's textarea
 * (which keeps focus, so the user never leaves the sentence they are writing).
 * That is also why there is no `role="dialog"` or focus trap here — it is an
 * ARIA combobox popup, and the input it belongs to owns the interaction.
 *
 * It renders as a sibling of the textarea rather than a portal: the composer's
 * card has no `transform`, so an absolutely positioned list is not clipped, and
 * a portal would put the listbox outside the `aria-controls`-described subtree
 * for no benefit.
 */
export function DocumentMentionPicker({
  id,
  query,
  results,
  loading,
  error,
  activeIndex,
  onSelect,
  onHover,
}: {
  /** The listbox's id — the textarea points `aria-controls` at it. */
  id: string;
  /** The text typed after the `@`, for the empty/loading copy. */
  query: string;
  results: FileMetadataResponse[];
  loading: boolean;
  error: string | null;
  activeIndex: number;
  onSelect: (file: FileMetadataResponse) => void;
  /** Pointer hover moves the highlight, so Enter picks what was hovered. */
  onHover: (index: number) => void;
}) {
  const trimmed = query.trim();

  return (
    <div className="absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-xl border border-outline-strong bg-surface shadow-2xl shadow-black/40">
      <div className="border-b border-outline px-3 py-2">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          {error ? "Search failed" : `Find a document`}
        </p>
      </div>

      {error ? (
        <p
          role="alert"
          className="flex items-start gap-2 px-3 py-3 text-xs text-error"
        >
          <WarningCircle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">{error}</span>
        </p>
      ) : loading ? (
        <p
          role="status"
          className="flex items-center gap-2 px-3 py-3 text-xs text-muted"
        >
          <CircleNotch size={14} className="animate-spin" aria-hidden />
          Searching your files…
        </p>
      ) : results.length === 0 ? (
        <p className="flex items-start gap-2 px-3 py-3 text-xs text-muted">
          <FileMagnifyingGlass size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0">
            {trimmed
              ? `No files match “${trimmed}”.`
              : "Start typing a file name to search your Transform Drive."}
          </span>
        </p>
      ) : (
        // `role="listbox"` + `aria-activedescendant` on the textarea is the
        // combobox pattern: the options are *not* focusable, the input keeps
        // focus, and the active row is announced from the textarea. Roving
        // focus here would steal the caret out of the draft on every arrow key.
        <ul id={id} role="listbox" aria-label="Files in your Transform Drive" className="max-h-64 overflow-y-auto p-1">
          {results.map((file, index) => {
            const active = index === activeIndex;
            const extension = attachmentExtension({ id: file.id, name: file.file_name });
            const size = file.file_size_bytes > 0 ? formatBytes(file.file_size_bytes) : null;
            const added = formatDateOrNull(file.created_at);
            return (
              <li key={file.id}>
                <div
                  id={`${id}-option-${index}`}
                  role="option"
                  aria-selected={active}
                  // Pointer selection must not blur the textarea before the
                  // click lands, or the picker would close under the cursor.
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => onSelect(file)}
                  onMouseEnter={() => onHover(index)}
                  className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 ${
                    active ? "bg-primary-container text-on-primary-container" : "hover:bg-surface-variant"
                  }`}
                >
                  <FormatThumb format={extension} size="sm" label="" className="shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={file.file_name}>
                      {file.file_name}
                    </p>
                    {/* Only facts the API returned: a missing size or date is
                        omitted rather than rendered as a placeholder. */}
                    <p className={`truncate text-xs ${active ? "text-on-primary-container/70" : "text-muted"}`}>
                      {[extension.toUpperCase(), size, added].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
