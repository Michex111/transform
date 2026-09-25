/**
 * The circular user tile: a picture when the account has one, initials
 * otherwise.
 *
 * The two branches read from `lib/avatar.ts` rather than indexing into the user
 * record, because that module already reproduces the server's naming rules for
 * the independent-deploy case (new bundle, older API). Duplicating the fallback
 * here is how "AL" in the shell and "AD" on the settings page happen.
 */

import { useState } from "react";
import type { UserResponse } from "@/api/types";
import { avatarSrc, initialsFor } from "@/lib/avatar";

interface AvatarProps {
  user?: UserResponse | null;
  /** Tile edge in pixels; the monogram is scaled from it. */
  size?: number;
  className?: string;
}

export function Avatar({ user, size = 32, className = "" }: AvatarProps) {
  const src = avatarSrc(user);
  // Track *which* URL failed rather than a plain boolean: if the account picks a
  // new picture, the new URL has to get a chance to render, and a sticky
  // `failed = true` would leave the tile showing initials forever.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = src !== null && src !== failedSrc;

  return (
    <span
      data-testid="avatar"
      data-variant={showImage ? "image" : "initials"}
      className={`flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary-container font-display font-semibold text-on-primary-container ${className}`}
      style={{ width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.36)) }}
    >
      {showImage ? (
        // Decorative on purpose: every call site either prints the name beside
        // the tile or puts the tile inside a button whose `aria-label` already
        // names the account. Announcing the picture too says it twice.
        //
        // The `onError` fallback is not belt-and-braces: the URL is a `data:`
        // image the server produced, and one that fails to decode (truncated
        // base64, a format the browser refuses) renders the browser's broken
        // image glyph inside a circle — which reads as a layout bug rather than
        // a missing picture.
        <img
          src={src}
          alt=""
          aria-hidden="true"
          onError={() => setFailedSrc(src)}
          className="h-full w-full object-cover"
        />
      ) : (
        initialsFor(user)
      )}
    </span>
  );
}
