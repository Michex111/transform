// The shared "Save to Drive" flow.
//
// Two pages offer it — a completed row on the Convert page and the details
// panel on History — and both need the same four steps: resolve the destination
// (the user's configured default, or a folder they picked), fetch the completed
// output, hand it to the background upload manager, and report honestly what
// happened. Duplicating that in both pages would let the two drift, and the
// trickiest part (a stale default folder falls back to the root *and says so*)
// is exactly the part that would rot first.

import { useCallback, useMemo, useState } from "react";
import { jobOutputFilename } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useUploads } from "@/uploads/uploadsContext";
import { canSaveToDrive, defaultSaveFolderId, saveQueuedMessage } from "@/lib/saveToDrive";
import type { UiJob } from "@/jobs/JobsContext";

export interface SaveToDrive {
  /**
   * The job whose output is being read, or null when nothing is in flight.
   *
   * One value, not a set: the rows disable their controls while it is set, so a
   * second save cannot start and quietly take over the first one's spinner.
   */
  savingId: string | null;
  /** Save into the account's configured default folder, or the root when unset. */
  saveToDefaultFolder: (job: UiJob) => Promise<void>;
  /** Save into a specific destination (null = the drive root). */
  saveToFolder: (
    job: UiJob,
    folderId: string | null,
    options?: { useAsDefault?: boolean },
  ) => Promise<void>;
}

/**
 * The three API calls this flow needs: reading a completed job's output bytes,
 * resolving/validating a destination folder, and persisting a new default.
 *
 * All three stay on the API client rather than being re-implemented here. That
 * placement is load-bearing for the first one in particular: a job's output is
 * only reachable through the job's own route, which serves either a pre-signed
 * URL or — when at-rest encryption is on — the DECRYPTED object via the
 * authenticated stream endpoint. The client owns that URL allowlist, the
 * API-origin resolution and the silent 401 refresh; reading the access token out
 * of `localStorage` in a page would duplicate the auth path and lose the refresh.
 */
export function useSaveToDrive(): SaveToDrive {
  const { api: client, user, setUser } = useAuth();
  const { addFiles } = useUploads();
  const { success, error } = useToast();
  const [savingId, setSavingId] = useState<string | null>(null);

  /**
   * Read a completed output and queue it for upload.
   *
   * The bytes are read in the browser and handed to the background upload
   * manager, which owns the upload session, the quota checks, the transfer and
   * the dock — the browser cannot write into a library folder by itself.
   *
   * `jobOutputFilename` is reused rather than re-derived: it prefers the
   * extension the worker actually produced, so a multi-page `pdf → png` job is
   * saved as the `.zip` container it really is instead of a corrupt `.png`.
   */
  const queue = useCallback(
    async (job: UiJob, folderId: string | null): Promise<boolean> => {
      setSavingId(job.job_id);
      try {
        const blob = await client.fetchConversionOutputBlob(job.job_id);
        addFiles(
          [
            new File([blob], jobOutputFilename(job), {
              type: blob.type || "application/octet-stream",
            }),
          ],
          folderId,
        );
        return true;
      } catch (err) {
        error(err instanceof Error ? err.message : "Could not save this file to your drive.");
        return false;
      } finally {
        setSavingId(null);
      }
    },
    [addFiles, client, error],
  );

  /** The queued-save toast, including the one case where the destination is unknown. */
  const announce = useCallback(
    (folderId: string | null, name: string | null, fellBackToRoot = false) => {
      if (folderId !== null && name === null) {
        // The folder disappeared between choosing it and saving, so naming a
        // destination would be a guess. Still say plainly that it is queued —
        // and that it is not finished — without claiming where it went.
        success("Saving in the background — progress is in the uploads dock.");
        return;
      }
      success(saveQueuedMessage(name ? { name } : null, { fellBackToRoot }));
    },
    [success],
  );

  const saveToDefaultFolder = useCallback(
    async (job: UiJob) => {
      if (!canSaveToDrive(job)) return;

      const configured = defaultSaveFolderId(user);
      if (configured === null) {
        // No preference at all: the root is the intended destination here, so
        // this is not a fallback and the message must not suggest one.
        if (await queue(job, null)) announce(null, null);
        return;
      }

      let name: string | null = null;
      try {
        // Doubles as the ownership check: the API answers 404 for a folder that
        // does not exist *or* belongs to another account, so a resolvable name
        // is proof this account may file into it.
        name = (await client.getFolderContents(configured)).folder.name?.trim() || null;
      } catch {
        // A stale preference. The file still has to be saved somewhere, so it
        // goes to the root — and the user is told, because silently relocating
        // it would make a broken preference look like a working one.
        if (await queue(job, null)) announce(null, null, true);
        return;
      }
      if (await queue(job, configured)) announce(configured, name);
    },
    [announce, client, queue, user],
  );

  const saveToFolder = useCallback(
    async (job: UiJob, folderId: string | null, options?: { useAsDefault?: boolean }) => {
      if (!canSaveToDrive(job)) return;

      // Resolved alongside the fetch rather than before it: the name is only
      // used for the toast, and the transfer must not wait on it. A failure is
      // swallowed here (the save itself still works) and surfaces as the
      // unnamed-but-honest message in `announce`.
      const namePromise: Promise<string | null> =
        folderId === null
          ? Promise.resolve(null)
          : client
              .getFolderContents(folderId)
              .then((contents) => contents.folder.name?.trim() || null)
              .catch(() => null);

      const queued = await queue(job, folderId);
      if (!queued) return;
      announce(folderId, await namePromise);

      // The dialog may have asked to make this the default as well. The save is
      // already in the dock, so a failure here is reported without ever
      // discarding it — the transfer is the thing the user asked for first.
      if (options?.useAsDefault) {
        try {
          setUser(await client.updateProfile({ default_save_folder_id: folderId }));
          success(
            folderId
              ? "Default save location updated"
              : "Default save location reset to My Drive",
          );
        } catch (err) {
          error(
            err instanceof Error
              ? `The file is saving, but the default folder could not be set. ${err.message}`
              : "The file is saving, but the default folder could not be set.",
          );
        }
      }
    },
    [announce, client, error, queue, setUser, success],
  );

  return useMemo(
    () => ({ savingId, saveToDefaultFolder, saveToFolder }),
    [savingId, saveToDefaultFolder, saveToFolder],
  );
}
