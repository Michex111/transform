import { useState } from "react";
import { X } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import { FormatThumb } from "@/components/FormatThumb";
import { attachmentExtension, attachmentLabel } from "@/lib/assistantAttachments";

/** How many chips a read-only list shows before offering a "+N more" toggle. */
const VISIBLE_LIMIT = 3;

/**
 * Chips for the files attached to a turn.
 *
 * Used in two places with two behaviours:
 *   - in the composer, each chip is removable (a real button with an
 *     `aria-label`), the full list is always shown;
 *   - inside a sent user message, the chips are read-only and clipped behind a
 *     `+N more` disclosure so a five-file turn cannot grow the bubble without
 *     bound.
 *
 * Both variants live here so the file is represented the same way whether it is
 * about to be sent or already in the transcript.
 */
export function AttachmentChips({
  attachments,
  onRemove,
  collapsible = false,
  limit = VISIBLE_LIMIT,
  tone = "surface",
  className = "",
}: {
  attachments: AssistantAttachment[];
  onRemove?: (id: string) => void;
  collapsible?: boolean;
  limit?: number;
  /** `onPrimary` sits inside a filled user bubble and inverts the contrast. */
  tone?: "surface" | "onPrimary";
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (attachments.length === 0) return null;

  const hiddenCount = collapsible ? attachments.length - limit : 0;
  const visible =
    expanded || hiddenCount <= 0 ? attachments : attachments.slice(0, limit);

  return (
    // `w-full min-w-0` lets the flex row wrap inside its container instead of
    // reporting the widest chip's intrinsic width and escaping the bubble.
    <ul
      className={`flex w-full min-w-0 flex-wrap gap-1 ${className}`}
      aria-label="Attached files"
    >
      {visible.map((attachment) => (
        <li key={attachment.id} className="min-w-0 max-w-full">
          <AttachmentChip attachment={attachment} onRemove={onRemove} tone={tone} />
        </li>
      ))}
      {hiddenCount > 0 && (
        <li className="min-w-0 max-w-full">
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className={`inline-flex min-w-0 max-w-full items-center rounded-lg border px-2 py-0.5 text-xs transition-colors pointer-coarse:min-h-11 ${
              tone === "onPrimary"
                ? "border-on-primary/30 text-on-primary/90 hover:border-on-primary/60"
                : "border-outline text-muted hover:border-primary/60 hover:text-on-background"
            }`}
          >
            {expanded ? "Show less" : `+${hiddenCount} more`}
          </button>
        </li>
      )}
    </ul>
  );
}

function AttachmentChip({
  attachment,
  onRemove,
  tone,
}: {
  attachment: AssistantAttachment;
  onRemove?: (id: string) => void;
  tone: "surface" | "onPrimary";
}) {
  const label = attachmentLabel(attachment);
  const format = attachmentExtension(attachment);

  return (
    <span
      // Compact by design: `py-0.5` + a 16px thumb keeps the chip around 24px so
      // a row of attachments does not dominate the composer — on a phone as much
      // as on a desktop.
      //
      // The remove button used to carry `pointer-coarse:min-h-11 min-w-11`, which
      // looked like "the visual height shrinks, the hit area does not" but did not
      // behave that way: `min-height` on a flex child sets the PARENT's height too,
      // so on a touch device the whole chip rendered 49px tall — taller on a phone
      // than on a desktop, exactly backwards. The hit area is now an absolutely
      // positioned `::before` on the button (see below), which cannot affect layout,
      // so the chip keeps its compact height while the ✕ keeps a real 44px target.
      className={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-lg border py-0.5 pl-1 pr-0.5 text-xs ${
        tone === "onPrimary"
          ? "border-on-primary/30 bg-black/10 text-on-primary"
          : "border-outline bg-surface-variant/60 text-on-background"
      }`}
    >
      <FormatThumb format={format} size="2xs" label="" className="shrink-0" />
      <span className="min-w-0 flex-1 truncate font-medium" title={label}>
        {label}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={() => onRemove(attachment.id)}
          aria-label={`Remove ${label}`}
          // `-inset-3.5` around the 16px button box is what makes the touch target
          // 44px: an invisible, layout-free expander. Sizing the button itself
          // instead is what made the chip tall. This is also why it is gated on
          // `pointer-coarse` — a mouse needs no such target.
          className={`relative -mr-0.5 shrink-0 rounded-md p-0.5 transition-colors pointer-coarse:before:absolute pointer-coarse:before:-inset-3.5 pointer-coarse:before:content-[''] ${
            tone === "onPrimary"
              ? "text-on-primary/80 hover:bg-black/15 hover:text-on-primary"
              : "text-muted hover:bg-surface-variant hover:text-on-background"
          }`}
        >
          <X size={12} weight="bold" />
        </button>
      )}
    </span>
  );
}
