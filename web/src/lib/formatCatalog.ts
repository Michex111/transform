// Format catalog grouped into the categories shown in the picker.
// Each format carries a canonical color (from the design system) and a short
// display label. Matches the reference image's Archive/Audio/Cad/Document etc.

export interface FormatDef {
  ext: string;
  label: string;
  color: string;
}

export interface FormatCategory {
  id: string;
  name: string;
  formats: FormatDef[];
  /** color to tint the "From/To" source card icon for this category */
  iconColor: string;
}

const color = (fmt: string) => `var(--color-fmt-${fmt})`;

export const FORMAT_CATEGORIES: FormatCategory[] = [
  {
    id: "archive",
    name: "Archive",
    iconColor: "var(--color-warning)",
    formats: [
      { ext: "7z", label: "7Z", color: color("text") },
      { ext: "ace", label: "ACE", color: color("text") },
      { ext: "alz", label: "ALZ", color: color("text") },
      { ext: "arc", label: "ARC", color: color("text") },
      { ext: "arj", label: "ARJ", color: color("text") },
      { ext: "bz", label: "BZ", color: color("text") },
      { ext: "bz2", label: "BZ2", color: color("text") },
      { ext: "cab", label: "CAB", color: color("text") },
      { ext: "cpio", label: "CPIO", color: color("text") },
      { ext: "deb", label: "DEB", color: color("text") },
      { ext: "dmg", label: "DMG", color: color("text") },
      { ext: "gz", label: "GZ", color: color("text") },
      { ext: "iso", label: "ISO", color: color("text") },
      { ext: "jar", label: "JAR", color: color("text") },
      { ext: "lha", label: "LHA", color: color("text") },
      { ext: "lz", label: "LZ", color: color("text") },
      { ext: "lzma", label: "LZMA", color: color("text") },
      { ext: "lzo", label: "LZO", color: color("text") },
      { ext: "rar", label: "RAR", color: color("text") },
      { ext: "rpm", label: "RPM", color: color("text") },
      { ext: "tar", label: "TAR", color: color("text") },
      { ext: "tar.gz", label: "TAR.GZ", color: color("text") },
      { ext: "tar.bz2", label: "TAR.BZ2", color: color("text") },
      { ext: "tar.xz", label: "TAR.XZ", color: color("text") },
      { ext: "xz", label: "XZ", color: color("text") },
      { ext: "zip", label: "ZIP", color: color("text") },
      { ext: "zst", label: "ZST", color: color("text") },
    ],
  },
  {
    id: "audio",
    name: "Audio",
    iconColor: "var(--color-fmt-audio)",
    formats: [
      { ext: "aac", label: "AAC", color: color("audio") },
      { ext: "aiff", label: "AIFF", color: color("audio") },
      { ext: "alac", label: "ALAC", color: color("audio") },
      { ext: "flac", label: "FLAC", color: color("audio") },
      { ext: "m4a", label: "M4A", color: color("audio") },
      { ext: "m4b", label: "M4B", color: color("audio") },
      { ext: "mid", label: "MID", color: color("audio") },
      { ext: "mp3", label: "MP3", color: color("audio") },
      { ext: "ogg", label: "OGG", color: color("audio") },
      { ext: "opus", label: "OPUS", color: color("audio") },
      { ext: "wav", label: "WAV", color: color("audio") },
      { ext: "wma", label: "WMA", color: color("audio") },
    ],
  },
  {
    id: "cad",
    name: "Cad",
    iconColor: "var(--color-fmt-image)",
    formats: [
      { ext: "dwg", label: "DWG", color: color("image") },
      { ext: "dxf", label: "DXF", color: color("image") },
      { ext: "dgn", label: "DGN", color: color("image") },
      { ext: "ifc", label: "IFC", color: color("image") },
      { ext: "stl", label: "STL", color: color("image") },
      { ext: "obj", label: "OBJ", color: color("image") },
      { ext: "3ds", label: "3DS", color: color("image") },
    ],
  },
  {
    id: "document",
    name: "Document",
    iconColor: "var(--color-fmt-pdf)",
    formats: [
      { ext: "pdf", label: "PDF", color: color("pdf") },
      { ext: "doc", label: "DOC", color: color("word") },
      { ext: "docx", label: "DOCX", color: color("word") },
      { ext: "odt", label: "ODT", color: color("word") },
      { ext: "rtf", label: "RTF", color: color("word") },
      { ext: "html", label: "HTML", color: color("text") },
      { ext: "pages", label: "PAGES", color: color("word") },
      { ext: "txt", label: "TXT", color: color("text") },
      { ext: "md", label: "MD", color: color("text") },
      { ext: "tex", label: "TEX", color: color("text") },
      { ext: "wp", label: "WP", color: color("word") },
    ],
  },
  {
    id: "ebook",
    name: "Ebook",
    iconColor: "var(--color-fmt-audio)",
    formats: [
      { ext: "azw", label: "AZW", color: color("audio") },
      { ext: "azw3", label: "AZW3", color: color("audio") },
      { ext: "epub", label: "EPUB", color: color("audio") },
      { ext: "fb2", label: "FB2", color: color("audio") },
      { ext: "mobi", label: "MOBI", color: color("audio") },
      { ext: "lit", label: "LIT", color: color("audio") },
    ],
  },
  {
    id: "font",
    name: "Font",
    iconColor: "var(--color-fmt-image)",
    formats: [
      { ext: "ttf", label: "TTF", color: color("image") },
      { ext: "otf", label: "OTF", color: color("image") },
      { ext: "woff", label: "WOFF", color: color("image") },
      { ext: "woff2", label: "WOFF2", color: color("image") },
      { ext: "eot", label: "EOT", color: color("image") },
    ],
  },
  {
    id: "image",
    name: "Image",
    iconColor: "var(--color-fmt-image)",
    formats: [
      { ext: "jpg", label: "JPG", color: color("image") },
      { ext: "jpeg", label: "JPEG", color: color("image") },
      { ext: "png", label: "PNG", color: color("image") },
      { ext: "webp", label: "WEBP", color: color("image") },
      { ext: "gif", label: "GIF", color: color("image") },
      { ext: "svg", label: "SVG", color: color("image") },
      { ext: "bmp", label: "BMP", color: color("image") },
      { ext: "tiff", label: "TIFF", color: color("image") },
      { ext: "heic", label: "HEIC", color: color("image") },
      { ext: "ico", label: "ICO", color: color("image") },
      { ext: "avif", label: "AVIF", color: color("image") },
    ],
  },
  {
    id: "presentation",
    name: "Presentation",
    iconColor: "var(--color-fmt-video)",
    formats: [
      { ext: "ppt", label: "PPT", color: color("video") },
      { ext: "pptx", label: "PPTX", color: color("video") },
      { ext: "odp", label: "ODP", color: color("video") },
      { ext: "key", label: "KEY", color: color("video") },
    ],
  },
  {
    id: "spreadsheet",
    name: "Spreadsheet",
    iconColor: "var(--color-fmt-excel)",
    formats: [
      { ext: "xls", label: "XLS", color: color("excel") },
      { ext: "xlsx", label: "XLSX", color: color("excel") },
      { ext: "ods", label: "ODS", color: color("excel") },
      { ext: "csv", label: "CSV", color: color("excel") },
      { ext: "numbers", label: "NUMBERS", color: color("excel") },
    ],
  },
  {
    id: "video",
    name: "Video",
    iconColor: "var(--color-fmt-video)",
    formats: [
      { ext: "mp4", label: "MP4", color: color("video") },
      { ext: "mov", label: "MOV", color: color("video") },
      { ext: "avi", label: "AVI", color: color("video") },
      { ext: "mkv", label: "MKV", color: color("video") },
      { ext: "webm", label: "WEBM", color: color("video") },
      { ext: "flv", label: "FLV", color: color("video") },
      { ext: "wmv", label: "WMV", color: color("video") },
      { ext: "mpg", label: "MPG", color: color("video") },
      { ext: "m4v", label: "M4V", color: color("video") },
      { ext: "3gp", label: "3GP", color: color("video") },
    ],
  },
];
