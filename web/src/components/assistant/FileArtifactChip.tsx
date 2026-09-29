import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Download, Eye, FolderSimple } from "@phosphor-icons/react";
import type { AssistantArtifact } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { FilePreviewModal } from "@/components/FilePreviewModal";
import { FormatThumb } from "@/components/FormatThumb";
import { RowMenu } from "@/components/RowMenu";
import {
  artifactFolderId,
  driveFolderHref,
  fileArtifactExtension,
} from "@/lib/fileArtifact";

/**
 * The chip class shared by every non-job artifact. Kept here (and exported) so
 * the file chip and the folder/unknown chips in `ArtifactChips` cannot drift.
 * `min-w-0`/`max-w-full` are load-bearing: without them the flex row reports an
 * intrinsic min-width of its widest child and overflows the message column.
 */
export const artifactChipClass =
  "inline-flex min-w-0 max-w-full items-center gap-2 rounded-lg border border-outline bg-surface-variant/60 px-2 py-1.5 text-xs transition-colors hover:border-primary/60";

/** A name that shrinks and truncates rather than pushing the chip wider. */
export const artifactChipNameClass =
  "min-w-0 flex-1 truncate font-medium text-on-background";

/**
 * A file the assistant surfaced, as a chip with its actions behind a ⋮.
 *
 * The chip is deliberately **not** a link. The three things a user wants after
 * "here is your file" are Preview, Download and Go to folder, and folding the
 * whole chip into "go to the library root" (as it used to) made the two most
 * valuable ones unreachable. A chip that both navigates and holds a menu is also
 * a trap on touch, where the menu and the row would fight for the same tap.
 *
 * The rules — where "Go to folder" points, which extension to show — live in
 * `@/lib/fileArtifact`, where they are unit-tested; this component only wires
 * them to the client calls (`downloadLibraryFile`) and the preview modal.
 */
export function FileArtifactChip({ artifact }: { artifact: AssistantArtifact }) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const navigate = useNavigate();
  // Open state is local: the modal belongs to the chip that opened it, and a
  // preview of one file must never be re-shown by an unrelated re-render.
  const [previewOpen, setPreviewOpen] = useState(false);
  // One flag so the Download item cannot be double-fired while its fetch runs.
  const [downloading, setDownloading] = useState(false);

  const id = artifact.id;
  const name = artifact.name || id;
  const extension = fileArtifactExtension(artifact);
  // Root-level files get this item too: it lands on "My Drive", which is where
  // the file actually is, so the action is truthful rather than hidden.
  const folderHref = driveFolderHref(artifactFolderId(artifact));

  async function download() {
    if (downloading || !id) return;
    setDownloading(true);
    try {
      // Never build the URL here: the client decides between a pre-signed URL
      // and the authenticated streaming path that decrypts an at-rest-encrypted
      // object, and owns the filename on the wire.
      await client.downloadLibraryFile(id, name);
    } catch (err) {
      // Surfaced, never swallowed: a menu item that silently does nothing on
      // failure reads as a broken button.
      toastError(err instanceof Error ? err.message : "Could not download this file");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className={artifactChipClass}>
      <FormatThumb format={extension} size="xs" label="" className="shrink-0" />
      <span className={artifactChipNameClass} title={name}>
        {name}
      </span>
      <RowMenu
        label={`More options for ${name}`}
        items={[
          {
            key: "preview",
            label: "Preview",
            icon: <Eye size={15} aria-hidden />,
            onSelect: () => setPreviewOpen(true),
          },
          {
            key: "download",
            label: downloading ? "Downloading…" : "Download",
            icon: <Download size={15} aria-hidden />,
            onSelect: () => void download(),
            disabled: downloading,
          },
          {
            key: "folder",
            label: "Go to folder",
            icon: <FolderSimple size={15} aria-hidden />,
            onSelect: () => navigate(folderHref),
          },
        ]}
      />
      {/* The modal fetches the bytes itself; an id and a name are enough. */}
      <FilePreviewModal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        file={{ id, file_name: name }}
      />
    </div>
  );
}
