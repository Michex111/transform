/**
 * How a user is presented: their picture, their name, and their initials.
 *
 * All of it is derived here rather than at the call sites because the same
 * questions are asked in several places (the sidebar, the mobile header, the
 * public header, the settings page) and they must never disagree — a shell
 * showing "AL" while the settings page shows "AD" reads as a bug.
 *
 * The server computes `display_name` and `initials` and sends them, and those
 * win when present. The fallbacks exist for the independent-deploy case (the
 * API and the SPA ship separately, so a new bundle can briefly talk to an API
 * that has never heard of these fields) and they are deliberately identical to
 * the server's own rule so the two can't visibly diverge.
 */

import type { UserResponse } from "@/api/types";

/** The image types the avatar endpoint accepts, for an `<input accept=…>`. */
export const AVATAR_ACCEPT = "image/png,image/jpeg,image/webp";

/**
 * Client-side upload ceiling, mirroring the API's own limit.
 *
 * Enforced locally only so an over-sized file is refused instantly instead of
 * after a 2 MB upload. The server re-checks; this is not a security control.
 */
export const AVATAR_MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

/** A person-shaped subset of the user record these helpers need. */
type NamedUser = Pick<UserResponse, "username"> &
  Partial<Pick<UserResponse, "first_name" | "last_name" | "display_name" | "initials" | "avatar_url">>;

/** `value` trimmed, or `null` when it is absent or only whitespace. */
function cleanName(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** True when the account has provided at least one of its two names. */
export function hasName(user: NamedUser | null | undefined): boolean {
  if (!user) return false;
  return cleanName(user.first_name) !== null || cleanName(user.last_name) !== null;
}

/**
 * The name to print beside the avatar.
 *
 * `"Ada Lovelace"` when both names are known, whichever single name is known
 * otherwise, and finally the username — every account created before names
 * existed, plus any account that has not filled them in yet.
 */
export function displayNameFor(user: NamedUser | null | undefined): string {
  if (!user) return "Account";
  const server = cleanName(user.display_name);
  if (server) return server;
  const parts = [cleanName(user.first_name), cleanName(user.last_name)].filter(
    (part): part is string => part !== null,
  );
  if (parts.length > 0) return parts.join(" ");
  return cleanName(user.username) ?? "Account";
}

/**
 * The two-character monogram shown when there is no picture.
 *
 * First letter of each name when available (so "Ada Lovelace" reads "AL"),
 * otherwise the first two letters of the username. Never empty, so the avatar
 * tile can't collapse into an anonymous blank circle.
 */
export function initialsFor(user: NamedUser | null | undefined): string {
  if (!user) return "??";
  const server = cleanName(user.initials);
  if (server) return server.slice(0, 2).toUpperCase();
  const fromNames =
    (cleanName(user.first_name)?.charAt(0) ?? "") + (cleanName(user.last_name)?.charAt(0) ?? "");
  if (fromNames) return fromNames.toUpperCase();
  const fromUsername = cleanName(user.username);
  return fromUsername ? fromUsername.slice(0, 2).toUpperCase() : "??";
}

/**
 * The `src` for the avatar `<img>`, or `null` to render the initials instead.
 *
 * Only a `data:` URL is accepted from the server. Anything else (a bare relative
 * path, an empty string, a value that is not a string at all) is treated as "no
 * picture": this is rendered straight into an `<img src>`, so passing an
 * unexpected value through would silently produce a request to a bogus URL
 * rather than a visible failure.
 */
export function avatarSrc(user: NamedUser | null | undefined): string | null {
  const url = user?.avatar_url;
  if (typeof url !== "string") return null;
  const trimmed = url.trim();
  return trimmed.startsWith("data:image/") ? trimmed : null;
}

/**
 * Reject an obviously unusable avatar file before uploading it.
 *
 * Returns a message to show the user, or `null` when the file may be sent. The
 * server repeats every check — this exists to give immediate feedback,
 * especially for a file the browser would happily spend a minute uploading.
 */
export function validateAvatarFile(file: File): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > AVATAR_MAX_UPLOAD_BYTES) {
    return `That image is ${formatMegabytes(file.size)} — the limit is 2 MB.`;
  }
  if (!isAcceptedAvatarType(file.type)) {
    return "Choose a PNG, JPEG, or WebP image.";
  }
  return null;
}

/**
 * Whether a MIME type is one the avatar pipeline accepts.
 *
 * An empty `file.type` (some pickers, and every file dragged in from an
 * unusual source) is allowed through: the server validates the actual bytes
 * with `file_magic`, so refusing here would block a valid image on the strength
 * of a missing hint. The reverse is not true — a declared type that is plainly
 * wrong is refused without a round trip.
 */
export function isAcceptedAvatarType(mimeType: string): boolean {
  if (!mimeType) return true;
  return mimeType === "image/png" || mimeType === "image/jpeg" || mimeType === "image/webp";
}

/** `2097152` → `"2.0 MB"`. Kept local: only the avatar limit needs it. */
function formatMegabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
