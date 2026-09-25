// The rules behind "Save to Drive".
//
// Every decision this feature makes *before* the transfer — whether a job can
// be filed at all, which folder the bytes land in, and what the user is told
// when the save is queued — is a pure function here. The transfer itself is the
// background upload manager's job (see `uploads/*`), so there is nothing
// DOM-shaped to test and nothing that can drift between the Convert page and
// the History panel.

/** The name the drive root is shown under. The API has no root folder object. */
export const ROOT_FOLDER_LABEL = "My Drive";

/**
 * The display name of a destination folder; the root (`My Drive`) when null.
 *
 * A blank name also reads as the root rather than as empty text: this string
 * ends up inside toast copy, where an empty name would leave a sentence with a
 * hole in it, and the root is where an unnameable destination effectively lands.
 */
export function folderLabel(folder: { name: string } | null | undefined): string {
  const name = folder?.name?.trim();
  return name ? name : ROOT_FOLDER_LABEL;
}

/**
 * The user's configured default save folder id, or null when unset/unusable.
 *
 * Only a non-empty, non-whitespace string counts: `undefined`, `null`, `""`,
 * `"  "` and a non-string from a malformed payload all mean the same thing —
 * "no preference" — and none of them is an error. `default_save_folder_id` is
 * optional on the wire because the API and the SPA deploy independently, so a
 * missing value must always degrade to the drive root rather than to a failed
 * save.
 *
 * The id is trimmed because an id never contains whitespace; a padded value
 * would only 404 the ownership check that follows it.
 */
export function defaultSaveFolderId(
  user: { default_save_folder_id?: string | null } | null | undefined,
): string | null {
  const value = user?.default_save_folder_id;
  if (typeof value !== "string") return null;
  const id = value.trim();
  return id === "" ? null : id;
}

/**
 * Whether a job's output can be filed into the drive (a completed conversion
 * only).
 *
 * `COMPLETED` is the terminal spelling the rest of the app uses (the status
 * badge, the download button, `showsCreditsUsed`). Nothing else has an output
 * to save: a `PENDING`/`PROCESSING` job has not produced one yet, and a
 * `FAILED` one never will — queueing an upload for either would only fill the
 * dock with a transfer that cannot succeed.
 */
export function canSaveToDrive(job: { status: string }): boolean {
  return job.status === "COMPLETED";
}

/**
 * The toast copy for a queued save.
 *
 * It deliberately does not say "Saved": the save is only *queued*, the
 * background upload manager does the transfer, and the uploads dock is where
 * its progress — and any failure — is reported. The destination is named
 * because it is the one thing the dock cannot tell the user, and `My Drive` is
 * what the root is called here.
 *
 * `fellBackToRoot` is set when the configured default folder could not be
 * resolved (deleted, or not owned by this account) and the file is being saved
 * to the root instead. Moving the file silently would make a stale preference
 * look like a working one, so the user is told where it actually went.
 */
export function saveQueuedMessage(
  folder: { name: string } | null,
  options?: { fellBackToRoot?: boolean },
): string {
  const label = folderLabel(folder);
  if (options?.fellBackToRoot) {
    return `Your default folder is unavailable, so this is saving to ${label}. Progress is in the uploads dock.`;
  }
  return `Saving to ${label} in the background — progress is in the uploads dock.`;
}
