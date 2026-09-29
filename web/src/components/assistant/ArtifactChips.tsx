import { useState } from "react";
import { Link } from "react-router-dom";
import { FileText, FolderSimple } from "@phosphor-icons/react";
import type { AssistantArtifact } from "@/api/types";
import { ConversionCard } from "@/components/assistant/ConversionCard";
import {
  FileArtifactChip,
  artifactChipClass as CHIP_CLASS,
  artifactChipNameClass as NAME_CLASS,
} from "@/components/assistant/FileArtifactChip";
import { folderHrefForArtifact } from "@/lib/fileArtifact";

/** How many chips a collapsed list shows before offering a "+N more" toggle. */
const VISIBLE_LIMIT = 4;

/**
 * Chips for the files, folders and jobs an assistant answer refers to.
 *
 * A *folder* chip is a real link into the drive (`/app/files?folder=…`), because
 * when the assistant says "I created a folder for you" the only useful next
 * action is to open it. A *file* chip is different: the three things a found
 * file is for — Preview, Download, Go to folder — live behind a ⋮ menu
 * (`FileArtifactChip`), so the chip itself must NOT also navigate. A *job* is
 * different again: the assistant has just started that conversion, so a link to
 * the queue would only send the user away from an answer they are still reading.
 * Jobs therefore get their own full-width `ConversionCard`, which shows live
 * progress and ends with Download / Save to Drive.
 *
 * The file chips are still capped behind a `+N more` disclosure so a turn that
 * surfaced a dozen files cannot grow the bubble without bound; the toggle is a
 * real button and the full list is always in the DOM while collapsed, so nothing
 * is hidden from a keyboard or screen-reader user without a control to reveal it.
 */
export function ArtifactChips({ artifacts }: { artifacts: AssistantArtifact[] }) {
  const [expanded, setExpanded] = useState(false);

  if (artifacts.length === 0) return null;

  // Jobs and files are laid out differently, so they are split before either is
  // capped: a card is never squeezed into a chip row.
  const jobs = artifacts.filter((artifact) => artifact.type === "job");
  const rest = artifacts.filter((artifact) => artifact.type !== "job");

  const hiddenCount = rest.length - VISIBLE_LIMIT;
  const visible = expanded || hiddenCount <= 0 ? rest : rest.slice(0, VISIBLE_LIMIT);

  return (
    <div className="w-full min-w-0 space-y-2">
      {jobs.length > 0 && (
        <ul className="w-full min-w-0 space-y-2" aria-label="Conversions">
          {jobs.map((artifact) => (
            <li key={`${artifact.type}:${artifact.id || artifact.name}`} className="min-w-0">
              <ConversionCard artifact={artifact} />
            </li>
          ))}
        </ul>
      )}

      {rest.length > 0 && (
        // `w-full min-w-0` is the fix for the overflow: without it the flex row
        // reports an intrinsic min-width of its widest chip and refuses to wrap
        // inside the bubble's `max-w-[85%]`.
        <ul className="flex w-full min-w-0 flex-wrap gap-2" aria-label="Referenced items">
          {visible.map((artifact) => (
            <li
              key={`${artifact.type}:${artifact.id || artifact.name}`}
              className="min-w-0 max-w-full"
            >
              <ArtifactChip artifact={artifact} />
            </li>
          ))}
          {hiddenCount > 0 && (
            <li className="min-w-0 max-w-full">
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
                className="inline-flex min-w-0 max-w-full items-center rounded-lg border border-outline px-2 py-1.5 text-xs text-muted transition-colors hover:border-primary/60 hover:text-on-background pointer-coarse:min-h-11"
              >
                {expanded ? "Show less" : `+${hiddenCount} more`}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * A non-job artifact as a chip. Jobs never reach here: they are rendered as
 * full-width `ConversionCard`s above, because a running conversion has live
 * progress and its own controls rather than a link away from the answer.
 */
function ArtifactChip({ artifact }: { artifact: AssistantArtifact }) {
  const name = artifact.name || artifact.id;

  if (artifact.type === "file") {
    return <FileArtifactChip artifact={artifact} />;
  }

  if (artifact.type === "folder") {
    // A real link, unlike the file chip: a folder has no bytes to preview or
    // download, so "open it" is the whole interaction. The label names the
    // destination so a screen reader hears where the link goes.
    return (
      <Link
        to={folderHrefForArtifact(artifact)}
        className={CHIP_CLASS}
        aria-label={name ? `Open folder ${name}` : "Open folder"}
      >
        <FolderSimple size={14} className="shrink-0 text-muted" aria-hidden />
        <span className={NAME_CLASS}>{name || "Folder"}</span>
      </Link>
    );
  }

  return (
    <span className={CHIP_CLASS}>
      <FileText size={14} className="shrink-0 text-muted" aria-hidden />
      <span className="min-w-0 flex-1 truncate font-medium text-muted">{name || "Item"}</span>
    </span>
  );
}
