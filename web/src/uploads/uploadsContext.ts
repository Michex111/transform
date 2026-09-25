/**
 * The uploads context object and its hook.
 *
 * Kept apart from `UploadsContext.tsx` (which exports only the provider
 * component) because the React Fast Refresh rule requires a module to export
 * either components or non-components, never both — exporting the hook
 * alongside the provider makes every edit to this feature fall back to a full
 * reload during development.
 */

import { createContext, useContext } from "react";
import type { UiUpload, UploadLimits } from "@/lib/uploadStore";

/**
 * How many files may transfer at once, across all of them.
 *
 * Bounded on purpose: a multi-file drop of large files otherwise opens a
 * connection per file (each with several parts of its own), and the rest of the
 * application's requests queue behind them. Two keep the pipe busy while
 * leaving room for the pages the user is still using.
 */
export const MAX_CONCURRENT_FILE_UPLOADS = 2;

export interface UploadsContextValue {
  /** Newest first. Includes finished rows until they are dismissed. */
  uploads: UiUpload[];
  /** Queue files for background upload. Returns immediately. */
  addFiles: (files: FileList | File[], folderId: string | null) => void;
  cancel: (id: string) => void;
  retry: (id: string) => void;
  dismiss: (id: string) => void;
  /** The server's advisory size/quota numbers, or `null` while unknown. */
  limits: UploadLimits;
  /** Re-read the dashboard's storage stats (called after each completion). */
  refreshLimits: () => void;
}

export const UploadsContext = createContext<UploadsContextValue | undefined>(undefined);

export function useUploads() {
  const context = useContext(UploadsContext);
  if (!context) throw new Error("useUploads must be used within UploadsProvider");
  return context;
}
