// Single source of truth for how a file format is represented visually:
// its color, its display label, its category and the Phosphor icon that
// stands in for it. Everything that renders a format thumbnail (FormatThumb,
// FileThumbnail, FormatChip, FormatMorph, Logo, PageLoader, Landing page)
// resolves through here so the mapping only exists once.

import type { ComponentType } from "react";
import {
  Article,
  Book,
  Cube,
  File,
  FileAudio,
  FileCode,
  FileImage,
  FileText,
  FileVideo,
  FileZip,
  Presentation,
  Table,
  TextAa,
  type IconProps,
} from "@phosphor-icons/react";
import { formatMeta } from "@/lib/format";
import { FORMAT_CATEGORIES } from "@/lib/formatCatalog";

export type FormatCategory =
  | "document"
  | "spreadsheet"
  | "presentation"
  | "image"
  | "audio"
  | "video"
  | "archive"
  | "ebook"
  | "font"
  | "cad"
  | "code"
  | "text";

/**
 * A Phosphor icon component (e.g. `FilePdf`). Typed against the package's own
 * `IconProps` so `size`, `weight`, `style`, `className` and `aria-hidden` are
 * all available to callers.
 */
export type FormatIconComponent = ComponentType<IconProps>;

export interface FormatVisual {
  /** Normalised (lowercase, dot-free) extension. */
  ext: string;
  /** Short display label, e.g. "PDF". */
  label: string;
  /** CSS color value, e.g. "var(--color-fmt-pdf)". */
  color: string;
  category: FormatCategory;
  Icon: FormatIconComponent;
}

/* ------------------------------------------------------------------ */
/* Category → icon                                                     */
/* ------------------------------------------------------------------ */

const CATEGORY_ICON: Record<FormatCategory, FormatIconComponent> = {
  archive: FileZip,
  audio: FileAudio,
  cad: Cube,
  code: FileCode,
  document: FileText,
  ebook: Book,
  font: TextAa,
  image: FileImage,
  presentation: Presentation,
  spreadsheet: Table,
  text: Article,
  video: FileVideo,
};

/**
 * Per-extension icon overrides.
 *
 * The tile's extension badge already spells the format out, so these glyphs
 * must NOT contain lettering of their own — Phosphor's `FilePdf`/`FileXls`/
 * `FilePpt`/`FileTxt` draw "PDF"/"XLS"/"PPT"/"TXT" inside the icon, which
 * would make a tile read "PDF PDF". Category is conveyed by color + glyph,
 * the extension by the badge.
 */
const EXT_ICON: Record<string, FormatIconComponent> = {};

/** Catalog category ids → normalised category union. */
const CATEGORY_MAP: Record<string, FormatCategory> = {
  archive: "archive",
  audio: "audio",
  cad: "cad",
  document: "document",
  ebook: "ebook",
  font: "font",
  image: "image",
  presentation: "presentation",
  spreadsheet: "spreadsheet",
  video: "video",
};

/**
 * Alias table for extensions the catalog does not carry (and a belt-and-braces
 * fallback for the common ones it does). The catalog always wins.
 */
const ALIASES: Record<string, FormatCategory> = {
  // documents
  doc: "document",
  docx: "document",
  odt: "document",
  pages: "document",
  rtf: "document",
  htm: "document",
  // text / code
  txt: "text",
  md: "text",
  js: "code",
  jsx: "code",
  ts: "code",
  tsx: "code",
  json: "code",
  py: "code",
  css: "code",
  html: "code",
  yml: "code",
  yaml: "code",
  sql: "code",
  sh: "code",
  rs: "code",
  go: "code",
  java: "code",
  c: "code",
  cpp: "code",
  h: "code",
  // spreadsheets
  xls: "spreadsheet",
  xlsx: "spreadsheet",
  csv: "spreadsheet",
  ods: "spreadsheet",
  numbers: "spreadsheet",
  // presentations
  ppt: "presentation",
  pptx: "presentation",
  odp: "presentation",
  key: "presentation",
  // images
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  bmp: "image",
  svg: "image",
  avif: "image",
  heic: "image",
  ico: "image",
  tif: "image",
  tiff: "image",
  // audio
  mp3: "audio",
  wav: "audio",
  ogg: "audio",
  flac: "audio",
  aac: "audio",
  m4a: "audio",
  opus: "audio",
  wma: "audio",
  aiff: "audio",
  mid: "audio",
  // video
  mp4: "video",
  mov: "video",
  avi: "video",
  mkv: "video",
  webm: "video",
  flv: "video",
  wmv: "video",
  mpg: "video",
  mpeg: "video",
  m4v: "video",
  "3gp": "video",
  // archives
  zip: "archive",
  tar: "archive",
  gz: "archive",
  bz2: "archive",
  rar: "archive",
  "7z": "archive",
  xz: "archive",
  iso: "archive",
  jar: "archive",
  dmg: "archive",
  zst: "archive",
  // compound archive extensions (the API uses dots, not hyphens: "tar.bz2")
  "tar.gz": "archive",
  "tar.bz2": "archive",
  "tar.xz": "archive",
  // ebooks
  epub: "ebook",
  mobi: "ebook",
  azw: "ebook",
  azw3: "ebook",
  // fonts
  woff: "font",
  woff2: "font",
  ttf: "font",
  otf: "font",
  // cad
  dwg: "cad",
  dxf: "cad",
};

/* ------------------------------------------------------------------ */
/* Catalog index (built once)                                          */
/* ------------------------------------------------------------------ */

interface CatalogEntry {
  label: string;
  color: string;
  category: FormatCategory;
}

const CATALOG_INDEX = new Map<string, CatalogEntry>();
for (const cat of FORMAT_CATEGORIES) {
  const category = CATEGORY_MAP[cat.id] ?? "text";
  for (const fmt of cat.formats) {
    CATALOG_INDEX.set(fmt.ext.toLowerCase(), { label: fmt.label, color: fmt.color, category });
  }
}

const CACHE = new Map<string, FormatVisual>();

/** Per-extension override → category icon → generic file icon. */
function iconFor(key: string, category: FormatCategory | undefined): FormatIconComponent {
  if (EXT_ICON[key]) return EXT_ICON[key];
  if (category && CATEGORY_ICON[category]) return CATEGORY_ICON[category];
  return File;
}

/** Normalise any user/API supplied extension: ".PDF" → "pdf". */
export function normalizeExt(ext: string): string {
  return (ext ?? "").toLowerCase().trim().replace(/^\./, "");
}

/**
 * Resolve the visual identity of a format extension.
 *
 * Priority: catalog → alias table → `formatMeta` (label + color) with the
 * `"text"` category. Results are memoized.
 */
export function formatVisual(ext: string): FormatVisual {
  const key = normalizeExt(ext);
  const cached = CACHE.get(key);
  if (cached) return cached;

  const catalog = CATALOG_INDEX.get(key);
  const meta = formatMeta(key);
  const label = (catalog?.label || meta.label || "FILE").toUpperCase();

  let visual: FormatVisual;
  if (catalog) {
    visual = {
      ext: key,
      label,
      color: catalog.color || meta.color,
      category: catalog.category,
      Icon: iconFor(key, catalog.category),
    };
  } else {
    const category: FormatCategory = ALIASES[key] ?? "text";
    visual = {
      ext: key,
      label,
      color: meta.color,
      category,
      Icon: iconFor(key, category),
    };
  }

  CACHE.set(key, visual);
  return visual;
}

/**
 * Build a translucent version of a format color.
 *
 * NOTE: appending an alpha hex suffix to a `var(--token)` color (the pattern
 * used elsewhere in the codebase, e.g. `${color}1a`) is a no-op in CSS — the
 * resulting declaration is invalid at computed-value time and gets dropped.
 * `color-mix()` produces a real alpha color instead.
 */
export function formatTint(color: string, percent: number, mixWith = "transparent"): string {
  return `color-mix(in srgb, ${color} ${percent}%, ${mixWith})`;
}
