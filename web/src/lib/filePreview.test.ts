// Tests for the preview classification rules.
//
// These rules decide whether the Files page hands a file's bytes to an `<img>`,
// an `<iframe>`, a `<video>`/`<audio>`, or a `<pre>` — and, crucially, whether
// it refuses to preview at all. The refusal path matters as much as the others:
// reading an unclassified binary into a `<pre>`, or a 200 MB log, is the failure
// mode these cases exist to pin.

import { describe, expect, it } from "vitest";
import {
  PREVIEW_TEXT_MAX_BYTES,
  extensionOf,
  isPreviewable,
  isTextPreviewOversize,
  previewKind,
  previewMimeType,
  previewUnavailableMessage,
  type PreviewKind,
} from "@/lib/filePreview";

describe("extensionOf", () => {
  it("lowercases the extension and drops the dot", () => {
    expect(extensionOf("Report.PDF")).toBe("pdf");
    expect(extensionOf("holiday.JPG")).toBe("jpg");
  });

  it("keeps only the last segment of a multi-dot name", () => {
    expect(extensionOf("archive.tar.gz")).toBe("gz");
  });

  it("returns an empty string when there is no dot", () => {
    expect(extensionOf("README")).toBe("");
    expect(extensionOf("")).toBe("");
  });

  it("treats a dotfile as having no extension", () => {
    // ".gitignore" must not be read as "a file of type gitignore", and a
    // ".png"-named dotfile must not borrow the image preview.
    expect(extensionOf(".gitignore")).toBe("");
    expect(extensionOf(".png")).toBe("");
  });

  it("returns an empty string for a trailing dot", () => {
    expect(extensionOf("weird.")).toBe("");
  });
});

describe("previewKind by extension", () => {
  it.each<[string, PreviewKind]>([
    ["photo.png", "image"],
    ["photo.jpg", "image"],
    ["photo.jpeg", "image"],
    ["anim.gif", "image"],
    ["logo.webp", "image"],
    ["bmp.bmp", "image"],
    ["vector.svg", "image"],
    ["next.avif", "image"],
    ["phone.heic", "image"],
    ["favicon.ico", "image"],
    ["scan.tiff", "image"],
    ["scan.tif", "image"],
    ["doc.pdf", "pdf"],
    ["clip.mp4", "video"],
    ["clip.webm", "video"],
    ["clip.mov", "video"],
    ["clip.mkv", "video"],
    ["clip.avi", "video"],
    ["clip.m4v", "video"],
    ["clip.ogv", "video"],
    // The video formats the picker advertises; classified so the browser can
    // accept or reject them, rather than refused up front.
    ["clip.flv", "video"],
    ["clip.wmv", "video"],
    ["clip.mpg", "video"],
    ["clip.mpeg", "video"],
    ["clip.3gp", "video"],
    ["song.mp3", "audio"],
    ["song.wav", "audio"],
    ["song.ogg", "audio"],
    ["song.oga", "audio"],
    ["song.flac", "audio"],
    ["song.m4a", "audio"],
    ["song.aac", "audio"],
    ["song.opus", "audio"],
    // Was the inconsistency: advertised as Audio but not classified as such,
    // so an `.aiff` was refused while an `.mp3` beside it played.
    ["song.aiff", "audio"],
    ["song.alac", "audio"],
    ["song.m4b", "audio"],
    ["song.mid", "audio"],
    ["song.midi", "audio"],
    ["song.wma", "audio"],
    ["notes.txt", "text"],
    ["rows.csv", "text"],
    ["rows.tsv", "text"],
    ["data.json", "text"],
    ["readme.md", "text"],
    ["readme.markdown", "text"],
    ["feed.xml", "text"],
    ["config.yaml", "text"],
    ["config.yml", "text"],
    ["server.log", "text"],
    ["page.html", "text"],
    ["page.htm", "text"],
    ["style.css", "text"],
    ["app.js", "text"],
    ["mod.mjs", "text"],
    ["com.cjs", "text"],
    ["types.ts", "text"],
    ["Widget.tsx", "text"],
    ["Widget.jsx", "text"],
    ["script.py", "text"],
    ["script.rb", "text"],
    ["main.go", "text"],
    ["main.rs", "text"],
    ["Main.java", "text"],
    ["index.php", "text"],
    ["run.sh", "text"],
    ["query.sql", "text"],
    ["setup.ini", "text"],
    ["Cargo.toml", "text"],
    ["doc.rst", "text"],
    ["paper.tex", "text"],
    ["subs.srt", "text"],
    ["subs.vtt", "text"],
  ])("classifies %s as %s", (fileName, kind) => {
    expect(previewKind(fileName)).toBe(kind);
  });

  it("is case-insensitive", () => {
    expect(previewKind("PHOTO.PNG")).toBe("image");
    expect(previewKind("Notes.TXT")).toBe("text");
    expect(previewKind("Clip.MP4")).toBe("video");
  });
});

describe("previewKind by MIME fallback", () => {
  it.each<[string | null | undefined, PreviewKind]>([
    ["image/png", "image"],
    ["application/pdf", "pdf"],
    ["video/mp4", "video"],
    ["audio/mpeg", "audio"],
    ["text/plain", "text"],
    ["application/json", "text"],
  ])("classifies an extensionless name with mime %s as %s", (mime, kind) => {
    // No dot in the name, so only the MIME type can classify the file.
    expect(previewKind("README", mime)).toBe(kind);
  });

  it("ignores a MIME charset parameter", () => {
    expect(previewKind("blob", "text/plain; charset=utf-8")).toBe("text");
    expect(previewKind("blob", "application/json; charset=utf-8")).toBe("text");
  });

  it("falls back to the MIME type for an unrecognised extension", () => {
    // The name says nothing we understand; the MIME type does.
    expect(previewKind("clip.bin", "video/mp4")).toBe("video");
    expect(previewKind("blob.dat", "application/pdf")).toBe("pdf");
  });

  it("returns none when there is nothing to go on", () => {
    expect(previewKind("README")).toBe("none");
    expect(previewKind("README", null)).toBe("none");
    expect(previewKind("README", "")).toBe("none");
    expect(previewKind("data.xyz", "application/octet-stream")).toBe("none");
    expect(previewKind("data.xyz")).toBe("none");
    expect(previewKind("installer.exe", "application/octet-stream")).toBe("none");
  });

  it("never previews a dotfile, even with a text MIME type", () => {
    // A dotfile has no extension to classify it; "gitignore" is not a format.
    expect(previewKind(".gitignore")).toBe("none");
    expect(previewKind(".gitignore", "text/plain")).toBe("none");
    expect(previewKind(".env", "text/plain")).toBe("none");
    expect(previewKind(".png", "image/png")).toBe("none");
  });
});

describe("previewKind precedence", () => {
  it("lets a known extension win over a misleading MIME type", () => {
    // The server often reports a generic or simply wrong `mime_type`; the name
    // is what the file actually is.
    expect(previewKind("notes.txt", "image/png")).toBe("text");
    expect(previewKind("invoice.pdf", "application/octet-stream")).toBe("pdf");
    expect(previewKind("clip.mp4", "application/octet-stream")).toBe("video");
  });

  it("keeps a name with no dot on the MIME-only path", () => {
    // The contrast with a dotfile: "README" has no extension at all, so the
    // MIME is the only signal and is used.
    expect(previewKind("README", "text/plain")).toBe("text");
  });
});

describe("isPreviewable", () => {
  it("refuses a .zip, the container a multi-page pdf -> jpg conversion emits", () => {
    // `pdf -> jpg` on a multi-page document produces a .zip of page images, not
    // an image. The Convert page has to gate on the *output's* name so those
    // rows get no preview button: previewing one would hand the browser bytes
    // that are not an image.
    expect(isPreviewable("report.zip")).toBe(false);
    expect(isPreviewable("report.zip", "application/zip")).toBe(false);
  });

  it("accepts the formats a browser renders inline", () => {
    expect(isPreviewable("photo.jpg")).toBe(true);
    expect(isPreviewable("doc.pdf")).toBe(true);
    expect(isPreviewable("song.mp3")).toBe(true);
  });

  it("refuses a dotfile, which has no format to preview", () => {
    expect(isPreviewable(".gitignore")).toBe(false);
    expect(isPreviewable(".gitignore", "text/plain")).toBe(false);
  });
});

describe("isTextPreviewOversize", () => {
  it("accepts a file exactly at the cap", () => {
    expect(isTextPreviewOversize(PREVIEW_TEXT_MAX_BYTES)).toBe(false);
  });

  it("rejects one byte over the cap", () => {
    expect(isTextPreviewOversize(PREVIEW_TEXT_MAX_BYTES + 1)).toBe(true);
  });

  it("rejects a large log well over the cap", () => {
    expect(isTextPreviewOversize(200 * 1024 * 1024)).toBe(true);
  });

  it("accepts small and empty files", () => {
    expect(isTextPreviewOversize(0)).toBe(false);
    expect(isTextPreviewOversize(1024)).toBe(false);
  });
});

describe("previewUnavailableMessage", () => {
  it("tells the user to download when the type cannot be previewed", () => {
    expect(previewUnavailableMessage("none")).toBe(
      "Preview isn't available for this file type. Download it to open it.",
    );
  });

  it("gives every kind its own short note", () => {
    const kinds: PreviewKind[] = ["image", "pdf", "video", "audio", "text", "none"];
    const messages = kinds.map((kind) => previewUnavailableMessage(kind));
    for (const message of messages) {
      expect(message.length).toBeGreaterThan(0);
      expect(message).toMatch(/[Dd]ownload it to open it\.$/);
    }
    expect(new Set(messages).size).toBe(kinds.length);
  });

  it("names the medium for a file the browser refused to decode", () => {
    // Reached when the element itself errors, so the copy has to say what
    // could not be played rather than falling back to "unknown file type".
    expect(previewUnavailableMessage("audio")).toContain("audio");
    expect(previewUnavailableMessage("video")).toContain("video");
    expect(previewUnavailableMessage("image")).toContain("image");
    expect(previewUnavailableMessage("pdf")).toContain("PDF");
  });
});

describe("previewMimeType", () => {
  // A blob URL's type comes from the Blob, so this is what every preview
  // element is handed. The API's `mime_type` is the object store's leftover:
  // measured on the live stack as `application/x-www-form-urlencoded` for
  // every file uploaded under the 100 MB multipart threshold.
  const STORAGE_DEFAULTS = ["application/x-www-form-urlencoded", "application/octet-stream"];

  it.each<[string, PreviewKind, string]>([
    ["song.mp3", "audio", "audio/mpeg"],
    ["song.wav", "audio", "audio/wav"],
    ["song.flac", "audio", "audio/flac"],
    ["song.m4a", "audio", "audio/mp4"],
    ["song.ogg", "audio", "audio/ogg"],
    ["song.opus", "audio", "audio/ogg"],
    ["song.aiff", "audio", "audio/aiff"],
    // ALAC and M4B are MP4 containers, like M4A.
    ["song.alac", "audio", "audio/mp4"],
    ["song.m4b", "audio", "audio/mp4"],
    ["song.wma", "audio", "audio/x-ms-wma"],
    ["song.mid", "audio", "audio/midi"],
    ["clip.mp4", "video", "video/mp4"],
    ["clip.webm", "video", "video/webm"],
    ["clip.mov", "video", "video/quicktime"],
    ["clip.wmv", "video", "video/x-ms-wmv"],
    ["clip.flv", "video", "video/x-flv"],
    ["clip.mpg", "video", "video/mpeg"],
    ["doc.pdf", "pdf", "application/pdf"],
    ["photo.png", "image", "image/png"],
    ["photo.jpg", "image", "image/jpeg"],
    ["vector.svg", "image", "image/svg+xml"],
    ["notes.txt", "text", "text/plain"],
    ["data.json", "text", "application/json"],
  ])("derives the type for %s from its name", (fileName, kind, expected) => {
    expect(previewMimeType(fileName, kind)).toBe(expected);
  });

  it("ignores the meaningless type storage reports", () => {
    for (const stored of STORAGE_DEFAULTS) {
      expect(previewMimeType("song.mp3", "audio", stored)).toBe("audio/mpeg");
      expect(previewMimeType("clip.mp4", "video", stored)).toBe("video/mp4");
      expect(previewMimeType("doc.pdf", "pdf", stored)).toBe("application/pdf");
      expect(previewMimeType("photo.png", "image", stored)).toBe("image/png");
    }
  });

  it("is case-insensitive", () => {
    expect(previewMimeType("Song.MP3", "audio")).toBe("audio/mpeg");
    expect(previewMimeType("PHOTO.PNG", "image")).toBe("image/png");
  });

  it("returns an empty type when the name says nothing we know", () => {
    // Empty means "work it out from the bytes". Handing over
    // `application/octet-stream` would instead *state* "unknown binary", which
    // a browser may act on.
    expect(previewMimeType("README", "none")).toBe("");
    expect(previewMimeType("data.xyz", "none")).toBe("");
    expect(previewMimeType("blob.unknownext", "audio")).toBe("");
    expect(previewMimeType("blob.unknownext", "audio", "application/octet-stream")).toBe("");
  });

  it("never returns a type belonging to a different kind", () => {
    // The server value is only adopted when it agrees with what we are about
    // to render, so a stray image type cannot be attached to an <audio>.
    expect(previewMimeType("blob.unknownext", "audio", "image/png")).toBe("");
    expect(previewMimeType("blob.unknownext", "video", "application/pdf")).toBe("");
    // ...and when it does agree, it is kept.
    expect(previewMimeType("blob.unknownext", "audio", "audio/ogg")).toBe("audio/ogg");
  });

  it("prefers the name over a disagreeing server value", () => {
    expect(previewMimeType("song.mp3", "audio", "application/pdf")).toBe("audio/mpeg");
  });

  it("strips a charset parameter from an adopted server value", () => {
    expect(previewMimeType("blob.unknownext", "text", "text/plain; charset=utf-8")).toBe(
      "text/plain",
    );
  });

  it("returns nothing for a dotfile, which never previews", () => {
    expect(previewMimeType(".gitignore", "none")).toBe("");
  });
});
