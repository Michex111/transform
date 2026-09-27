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

/** Containers the `<video>` element can usually play.
 *
 *  The set mirrors the formats the picker advertises as Video (see
 *  `FORMAT_CATEGORIES` in `@/lib/formatCatalog`) rather than the subset known to
 *  play everywhere. Classifying optimistically is the correct call because the
 *  modal now recovers when the browser refuses the bytes (`handleRenderError`):
 *  an FLV or WMV the user can actually decode gets a player, and one that
 *  cannot gets "Preview isn't available for this video" plus the Download
 *  button. Pre-emptively refusing all of them would have hidden the working
 *  cases for no benefit. */
const VIDEO_EXTENSIONS = new Set([
  "mp4",
  "m4v",
  "webm",
  "mov",
  "mkv",
  "avi",
  "ogv",
  "flv",
  "wmv",
  "mpg",
  "mpeg",
  "3gp",
]);

/** Containers the `<audio>` element can usually play.
 *
 *  Mirrors the picker's Audio category for the same reason as
 *  `VIDEO_EXTENSIONS`. `aiff`/`wma`/`alac`/`m4b`/`mid` were the gap that made
 *  preview inconsistent: all five were advertised as Audio, yet none of them
 *  matched here, so an `.aiff` fell through the MIME path (which the API could
 *  not answer for, since it was reporting the object store's default) and the
 *  user was told the file type could not be previewed while an `.mp3` beside it
 *  played fine. */
const AUDIO_EXTENSIONS = new Set([
  "mp3",
  "wav",
  "ogg",
  "oga",
  "flac",
  "m4a",
  "aac",
  "opus",
  "aiff",
  "alac",
  "m4b",
  "mid",
  "midi",
  "wma",
]);

/**
 * The MIME type a preview should declare for a file, by extension.
 *
 * WHY this table exists instead of trusting the API's `mime_type`: the value
 * the server stores is the *object store's* content type, not the file's. The
 * single-PUT upload path is signed without a `Content-Type` (sending one breaks
 * the signature — see `putToPresignedUrl`), so the provider stores its own
 * default and the API echoes that back. Measured on the live stack: every
 * file under the 100 MiB multipart threshold came back as
 * `application/x-www-form-urlencoded`, while multipart uploads came back as
 * `application/octet-stream` — so the *same* song was described differently
 * depending on its size. Both are meaningless to a media element or a PDF
 * viewer, and a `<blob:>` URL inherits its type from the Blob, so the wrong
 * one is what every preview used to see.
 *
 * The name is the one thing we can trust, so it decides — exactly the
 * precedence `previewKind` already uses.
 */
const MIME_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  // images
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
  svg: "image/svg+xml",
  avif: "image/avif",
  heic: "image/heic",
  ico: "image/x-icon",
  tiff: "image/tiff",
  tif: "image/tiff",
  // video
  mp4: "video/mp4",
  m4v: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  mkv: "video/x-matroska",
  avi: "video/x-msvideo",
  ogv: "video/ogg",
  flv: "video/x-flv",
  wmv: "video/x-ms-wmv",
  mpg: "video/mpeg",
  mpeg: "video/mpeg",
  "3gp": "video/3gpp",
  // audio
  mp3: "audio/mpeg",
  wav: "audio/wav",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  flac: "audio/flac",
  m4a: "audio/mp4",
  aac: "audio/aac",
  // Ogg Opus, as distinct from the (rarely used) bare `audio/opus`.
  opus: "audio/ogg",
  // ALAC and the M4B audiobook are both MP4 containers; the container is what
  // a media element keys off.
  alac: "audio/mp4",
  m4b: "audio/mp4",
  aiff: "audio/aiff",
  wma: "audio/x-ms-wma",
  mid: "audio/midi",
  midi: "audio/midi",
  // text
  txt: "text/plain",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  json: "application/json",
  md: "text/markdown",
  markdown: "text/markdown",
  xml: "application/xml",
  yaml: "text/yaml",
  yml: "text/yaml",
  log: "text/plain",
  html: "text/html",
  htm: "text/html",
  css: "text/css",
  js: "text/javascript",
  mjs: "text/javascript",
  cjs: "text/javascript",
  ts: "text/plain",
  tsx: "text/plain",
  jsx: "text/plain",
  srt: "text/plain",
  vtt: "text/vtt",
};

/**
 * Content types that say "we did not bother to work this out" and must never be
 * passed off as a real media type.
 *
 * `application/x-www-form-urlencoded` is not a mistake in this list: it is what
 * the object store actually reports for an untyped PUT, and it is the value
 * most of the library carries. Treating it as authoritative would label every
 * file — audio included — as a form submission.
 */
const GENERIC_MIME_TYPES = new Set([
  "",
  "application/octet-stream",
  "binary/octet-stream",
  "application/x-www-form-urlencoded",
  "application/unknown",
  "application/binary",
  "unknown/unknown",
]);

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
 * Whether a file can be previewed in-page at all.
 *
 * WHY this exists beside `previewKind` rather than at each call site: the
 * Convert page has to decide *before* opening anything whether to offer a
 * preview, and that decision must be the same one the modal makes when it
 * renders. A second list of "previewable extensions" on the page would drift
 * from this classifier's — it already did once, when five advertised audio
 * formats were missing from it — so the gate reuses the one classification.
 */
export function isPreviewable(fileName: string, mimeType?: string | null): boolean {
  return previewKind(fileName, mimeType) !== "none";
}

/**
 * The content type to give the Blob handed to an `<img>`, `<audio>`, `<video>`
 * or `<iframe>`, or `""` when we genuinely do not know (an empty type makes the
 * element fall back to sniffing the bytes, which is better than a wrong one).
 *
 * The name wins, for the same reason it wins in `previewKind` and because the
 * API's `mime_type` is the object store's content type rather than the file's
 * (see `MIME_BY_EXTENSION`). Only when the name has no extension we recognise
 * is the server's value considered at all — and then only if it actually
 * describes the kind we are about to render, so a stray
 * `application/x-www-form-urlencoded` can never be handed to an `<audio>`.
 *
 * Returning `""` rather than, say, `"application/octet-stream"` is deliberate:
 * `application/octet-stream` is a *statement* ("unknown binary") that a browser
 * may act on, whereas an empty type means "work it out from the bytes".
 */
export function previewMimeType(
  fileName: string,
  kind: PreviewKind,
  mimeType?: string | null,
): string {
  if (kind === "none") return "";
  const byExtension = MIME_BY_EXTENSION[extensionOf(fileName)];
  if (byExtension) return byExtension;

  const server = (mimeType ?? "").split(";")[0].trim().toLowerCase();
  if (GENERIC_MIME_TYPES.has(server)) return "";
  // Belt and braces: a type that classifies to a *different* kind than the one
  // we are rendering is not a type we should attach to that element.
  return kindFromMime(server) === kind ? server : "";
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
 * Reached two ways: a file we refuse to open at all (`none`), and a file we
 * *tried* to render that the browser then refused to decode — an audio or video
 * container whose codec this browser does not ship, or an image format it
 * cannot paint. The second case is why these exist per kind: "we cannot play
 * this audio here" is a different, more useful sentence than "unknown file
 * type", and before this it did not exist for audio at all (every audio file
 * was rendered as a player, so a decode failure left a dead control with no
 * explanation and no hint that Download was still an option).
 *
 * The `text` wording names size because the oversize guard is the only caller
 * that reaches it: a text file that failed for any other reason shows the fetch
 * error instead.
 */
export function previewUnavailableMessage(kind: PreviewKind): string {
  return UNAVAILABLE_MESSAGES[kind];
}
