// Rules for the in-page file preview on the Files page.
//
// Pure and DOM-free on purpose: the modal that consumes these rules needs
// `Blob`, `URL.createObjectURL`, a fetch and a real DOM to run, and this repo
// has **no jsdom**, so anything living inside that component is untestable
// here. Classifying a file — "can the browser show this, and how?" — is plain
// string logic, so it lives in this module where vitest can pin every branch.

/** How (if at all) the browser can render a file inline. */
export type PreviewKind = "image" | "pdf" | "video" | "audio" | "text" | "none";

/**
 * Largest text file we are willing to pull into memory and put in a `<pre>`.
 *
 * 512 KB is comfortably more than a log excerpt or a CSV a person wants to
 * eyeball, while still far below the size at which `blob.text()` + a single
 * text node starts to jank the main thread (a 200 MB log must never take this
 * path). Files above this show the "download it" copy instead.
 */
export const PREVIEW_TEXT_MAX_BYTES = 512 * 1024;

// Extension sets. These deliberately overlap with `formatExt` / `fileNameExtension`
// in `@/lib/format` in spirit but are *not* the same function: `formatExt` falls
// back to `"txt"` for anything it cannot classify, which would make every
// unknown binary look previewable as text. Here an unclassified file must stay
// unclassified.

/** `image/*` formats a browser can paint directly (incl. svg, which is why
 *  these are safe: it is served from a blob URL in an `<img>`, never as an
 *  inline document). */
const IMAGE_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "svg",
  "avif",
  "heic",
  "ico",
  "tiff",
  "tif",
]);

/** Containers the `<video>` element can usually play. */
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "mkv", "avi", "m4v", "ogv"]);

/** Containers the `<audio>` element can usually play. */
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"]);

/** Plain-text-ish formats worth showing in a `<pre>`. */
const TEXT_EXTENSIONS = new Set([
  "txt",
  "csv",
  "tsv",
  "json",
  "md",
  "markdown",
  "xml",
  "yaml",
  "yml",
  "log",
  "html",
  "htm",
  "css",
  "js",
  "mjs",
  "cjs",
  "ts",
  "tsx",
  "jsx",
  "py",
  "rb",
  "go",
  "rs",
  "java",
  "php",
  "sh",
  "sql",
  "ini",
  "toml",
  "rst",
  "tex",
  "srt",
  "vtt",
]);

/**
 * The lowercased extension segment of a file name, without the dot, or `""`
 * when the name has none.
 *
 * `"Report.PDF"` → `"pdf"`, `"archive.tar.gz"` → `"gz"`, `"README"` → `""`,
 * `"trailing."` → `""`.
 *
 * A **dotfile** (a leading dot with no other dot, `.gitignore`) has no
 * extension and returns `""` — treating it as "extension = gitignore" would
 * both invent a format the file does not have and, for `.bashrc`-style names,
 * let any leading-dot file borrow a real format's preview.
 */
export function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  // `dot <= 0` covers "no dot at all" (-1) and a dotfile (0).
  if (dot <= 0) return "";
  return fileName.slice(dot + 1).toLowerCase();
}

/** True for a name whose only dot is a leading one (`.gitignore`, `.env`). */
function isDotfileName(fileName: string): boolean {
  return fileName.startsWith(".") && !fileName.slice(1).includes(".");
}

/** Classify by a lowercased extension. `"none"` means "not recognised". */
function kindFromExtension(ext: string): PreviewKind {
  if (!ext) return "none";
  if (ext === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  if (TEXT_EXTENSIONS.has(ext)) return "text";
  return "none";
}

/** Classify by a MIME type, used only when the name yielded nothing. */
function kindFromMime(mimeType: string | null | undefined): PreviewKind {
  if (!mimeType) return "none";
  // Strip any `; charset=…` parameter before matching.
  const mime = mimeType.split(";")[0].trim().toLowerCase();
  if (!mime) return "none";
  if (mime.startsWith("image/")) return "image";
  if (mime === "application/pdf") return "pdf";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("text/") || mime === "application/json") return "text";
  return "none";
}

/**
 * How to preview `fileName`, or `"none"` when the browser cannot render it
 * inline.
 *
 * The **name decides first**: an extension we recognise wins outright, because
 * a server-reported `mime_type` is often the generic `application/octet-stream`
 * for a file whose name says exactly what it is. Only when the name yields
 * nothing (a name with no dot, or an extension we do not know) do we fall back
 * to the MIME type. Case-insensitive throughout.
 *
 * A **dotfile** (`.gitignore`) is the one name that never previews: it has no
 * extension to classify, and its MIME type is no help either — treating
 * "gitignore" as an extension would invent a format, and `.env`-style names are
 * better served by an explicit download than by a guess. So a dotfile is
 * `"none"` whether or not the server reports a text MIME.
 */
export function previewKind(fileName: string, mimeType?: string | null): PreviewKind {
  if (isDotfileName(fileName)) return "none";
  const byExtension = kindFromExtension(extensionOf(fileName));
  if (byExtension !== "none") return byExtension;
  return kindFromMime(mimeType);
}

/**
 * Whether a text file is too big to fetch into memory for a `<pre>`.
 *
 * Strictly greater than the cap: a file of exactly `PREVIEW_TEXT_MAX_BYTES` is
 * previewable, so the boundary is not ambiguous.
 */
export function isTextPreviewOversize(sizeBytes: number): boolean {
  return sizeBytes > PREVIEW_TEXT_MAX_BYTES;
}

// A `Record<PreviewKind, string>` rather than a `switch`: adding a kind to the
// union then fails to compile here until its copy exists, instead of silently
// falling off the end of the function and rendering `undefined`.
const UNAVAILABLE_MESSAGES: Record<PreviewKind, string> = {
  image: "Preview isn't available for this image here. Download it to open it.",
  pdf: "Preview isn't available for this PDF here. Download it to open it.",
  video: "Preview isn't available for this video here. Download it to open it.",
  audio: "Preview isn't available for this audio file here. Download it to open it.",
  text: "This text file is too large to preview here. Download it to open it.",
  none: "Preview isn't available for this file type. Download it to open it.",
};

/**
 * The plain-language reason a preview is not on screen, for the kind in
 * question. No marketing voice — this is only ever read by someone whose file
 * did not render.
 *
 * The `text` wording names size because the oversize guard is the only caller
 * that reaches it: a text file that failed for any other reason shows the
 * fetch error instead.
 */
export function previewUnavailableMessage(kind: PreviewKind): string {
  return UNAVAILABLE_MESSAGES[kind];
}
