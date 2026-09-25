// Tests for the identity helpers that decide what a user is called and what
// their avatar shows.
//
// This logic has to be right in two directions at once: it must use the
// server's answer when there is one (so the API stays the source of truth) and
// reproduce that same answer when the API is too old to send it (because the
// SPA and the API deploy independently, so "new bundle, old API" is a real
// state rather than a hypothetical). The second half of every block pins the
// fallback to the server's rule.

import { describe, expect, it } from "vitest";
import type { UserResponse } from "@/api/types";
import {
  AVATAR_MAX_UPLOAD_BYTES,
  avatarSrc,
  displayNameFor,
  hasName,
  initialsFor,
  isAcceptedAvatarType,
  validateAvatarFile,
} from "@/lib/avatar";

/** A user record with everything unset, so each test states only what it uses. */
function user(overrides: Partial<UserResponse> = {}): UserResponse {
  return {
    id: 1,
    username: "jdoe",
    email: "jdoe@example.com",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("displayNameFor", () => {
  it("prefers the server-computed name", () => {
    expect(displayNameFor(user({ display_name: "Ada Lovelace" }))).toBe("Ada Lovelace");
  });

  it("joins the names itself when the API does not compute one", () => {
    expect(displayNameFor(user({ first_name: "Ada", last_name: "Lovelace" }))).toBe("Ada Lovelace");
  });

  it("uses a single name when only one is set", () => {
    expect(displayNameFor(user({ first_name: "Ada" }))).toBe("Ada");
    expect(displayNameFor(user({ last_name: "Lovelace" }))).toBe("Lovelace");
  });

  it("falls back to the username for an account that never gave a name", () => {
    // Every account created before names existed looks like this.
    expect(displayNameFor(user())).toBe("jdoe");
    expect(displayNameFor(user({ first_name: null, last_name: null }))).toBe("jdoe");
  });

  it("treats whitespace-only names as absent", () => {
    expect(displayNameFor(user({ first_name: "   ", last_name: "\t" }))).toBe("jdoe");
    expect(displayNameFor(user({ display_name: "  " }))).toBe("jdoe");
  });

  it("trims instead of printing padded names", () => {
    expect(displayNameFor(user({ first_name: " Ada ", last_name: " Lovelace " }))).toBe(
      "Ada Lovelace",
    );
  });

  it("never renders an empty label", () => {
    expect(displayNameFor(user({ username: "" }))).toBe("Account");
    expect(displayNameFor(null)).toBe("Account");
    expect(displayNameFor(undefined)).toBe("Account");
  });
});

describe("initialsFor", () => {
  it("prefers the server-computed initials", () => {
    expect(initialsFor(user({ initials: "AL" }))).toBe("AL");
  });

  it("derives one letter from each name", () => {
    expect(initialsFor(user({ first_name: "Ada", last_name: "Lovelace" }))).toBe("AL");
  });

  it("derives a single initial when only one name is set", () => {
    expect(initialsFor(user({ first_name: "Ada" }))).toBe("A");
  });

  it("falls back to the first two letters of the username", () => {
    // The presentation every account had before this feature — unchanged, so an
    // existing user's avatar does not visually jump.
    expect(initialsFor(user())).toBe("JD");
  });

  it("treats whitespace-only names as absent", () => {
    expect(initialsFor(user({ first_name: " ", last_name: " " }))).toBe("JD");
  });

  it("never renders an empty monogram", () => {
    expect(initialsFor(user({ username: "" }))).toBe("??");
    expect(initialsFor(null)).toBe("??");
  });

  it("keeps a non-Latin name readable rather than empty", () => {
    expect(initialsFor(user({ first_name: "Ada", last_name: "Лавлейс" }))).toBe("AЛ");
  });
});

describe("hasName", () => {
  it("is true only when a real name is present", () => {
    expect(hasName(user({ first_name: "Ada" }))).toBe(true);
    expect(hasName(user({ first_name: "  " }))).toBe(false);
    expect(hasName(user())).toBe(false);
    expect(hasName(null)).toBe(false);
  });
});

describe("avatarSrc", () => {
  it("passes a data URL through", () => {
    const dataUrl = "data:image/webp;base64,UklGRiIAAABXRUJQVlA4IBYAAADwAQCdASo=";
    expect(avatarSrc(user({ avatar_url: dataUrl }))).toBe(dataUrl);
  });

  it("returns null when there is no picture", () => {
    expect(avatarSrc(user())).toBeNull();
    expect(avatarSrc(user({ avatar_url: null }))).toBeNull();
    expect(avatarSrc(null)).toBeNull();
  });

  it("refuses anything that is not an inline image", () => {
    // Rendered straight into <img src>: passing an unexpected value through
    // would issue a request to a bogus URL instead of failing visibly, so a
    // relative path, an empty string, a remote URL and a non-string all mean
    // "no picture" and let the initials tile show.
    expect(avatarSrc(user({ avatar_url: "" }))).toBeNull();
    expect(avatarSrc(user({ avatar_url: "   " }))).toBeNull();
    expect(avatarSrc(user({ avatar_url: "/api/users/me/avatar" }))).toBeNull();
    expect(avatarSrc(user({ avatar_url: "https://cdn.example.com/a.webp" }))).toBeNull();
    expect(
      avatarSrc(user({ avatar_url: "" as unknown as string | null })),
    ).toBeNull();
    expect(
      avatarSrc({
        username: "jdoe",
        avatar_url: 42 as unknown as string | null,
      }),
    ).toBeNull();
  });
});

describe("isAcceptedAvatarType", () => {
  it("accepts the three types the pipeline converts", () => {
    expect(isAcceptedAvatarType("image/png")).toBe(true);
    expect(isAcceptedAvatarType("image/jpeg")).toBe(true);
    expect(isAcceptedAvatarType("image/webp")).toBe(true);
  });

  it("accepts a missing type, deferring to the server's byte check", () => {
    // Some pickers report no type at all. The server validates the real bytes
    // with `file_magic`, so refusing on a missing hint would block a valid image.
    expect(isAcceptedAvatarType("")).toBe(true);
  });

  it("refuses a type the pipeline cannot decode", () => {
    expect(isAcceptedAvatarType("image/gif")).toBe(false);
    expect(isAcceptedAvatarType("image/svg+xml")).toBe(false);
    expect(isAcceptedAvatarType("application/pdf")).toBe(false);
  });
});

describe("validateAvatarFile", () => {
  function file(name: string, type: string, bytes: number): File {
    return new File([new Uint8Array(bytes)], name, { type });
  }

  it("accepts a reasonable image", () => {
    expect(validateAvatarFile(file("me.png", "image/png", 64 * 1024))).toBeNull();
  });

  it("refuses an empty file", () => {
    expect(validateAvatarFile(file("me.png", "image/png", 0))).toMatch(/empty/i);
  });

  it("names the actual size when the file is too large", () => {
    const message = validateAvatarFile(
      file("me.png", "image/png", AVATAR_MAX_UPLOAD_BYTES + 1),
    );
    expect(message).toMatch(/2\.0 MB/);
  });

  it("allows a file exactly at the limit", () => {
    expect(validateAvatarFile(file("me.png", "image/png", AVATAR_MAX_UPLOAD_BYTES))).toBeNull();
  });

  it("refuses an unsupported format before spending an upload on it", () => {
    expect(validateAvatarFile(file("me.gif", "image/gif", 1024))).toMatch(/PNG, JPEG, or WebP/);
  });
});
