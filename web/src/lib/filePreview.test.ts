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
  isTextPreviewOversize,
  previewKind,
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
    ["song.mp3", "audio"],
    ["song.wav", "audio"],
    ["song.ogg", "audio"],
    ["song.flac", "audio"],
    ["song.m4a", "audio"],
    ["song.aac", "audio"],
    ["song.opus", "audio"],
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
});
