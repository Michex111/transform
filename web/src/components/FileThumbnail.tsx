import {
  FilePdf,
  FileText,
  FileImage,
  FileXls,
  FileAudio,
  FileVideo,
  FileCode,
  FileZip,
  File,
} from "@phosphor-icons/react";
import { formatMeta, formatExt } from "@/lib/format";

/** A thumbnail for a file card, styled by the file's format.
 *  For image files (png/jpg/webp/gif) it shows the actual image; otherwise it
 *  shows a colored tile with the format's canonical color + icon. */
export function FileThumbnail({ fileName, mimeType, url, className = "aspect-square" }: {
  fileName: string;
  mimeType: string;
  url?: string | null;
  /** Override the default aspect ratio (e.g. "aspect-[4/3]" to make it shorter). */
  className?: string;
}) {
  const ext = formatExt(fileName, mimeType);
  const meta = formatMeta(ext);
  const isImage = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "avif"].includes(ext);

  if (isImage && url) {
    return (
      <div className={`relative w-full overflow-hidden rounded-t-xl bg-surface-variant ${className}`}>
        <img src={url} alt={fileName} className="h-full w-full object-contain" />
      </div>
    );
  }

  const Icon = iconFor(ext);
  return (
    <div
      className={`relative flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-t-xl ${className}`}
      style={{
        background: `linear-gradient(160deg, ${meta.color}22, ${meta.color}08)`,
      }}
    >
      <Icon size={40} weight="duotone" style={{ color: meta.color }} />
      <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase"
        style={{ color: meta.color, borderColor: `${meta.color}40`, background: `${meta.color}14` }}>
        {meta.label}
      </span>
    </div>
  );
}

function iconFor(ext: string) {
  switch (ext) {
    case "pdf": return FilePdf;
    case "doc":
    case "docx":
    case "odt":
    case "rtf":
    case "txt":
    case "md": return FileText;
    case "xls":
    case "xlsx":
    case "csv":
    case "ods":
    case "numbers": return FileXls;
    case "png": case "jpg": case "jpeg": case "webp": case "gif":
    case "bmp": case "svg": case "avif": case "heic": case "ico": return FileImage;
    case "mp3": case "wav": case "ogg": case "flac": case "aac":
    case "m4a": case "opus": case "wma": case "aiff": case "mid": return FileAudio;
    case "mp4": case "mov": case "avi": case "mkv": case "webm":
    case "flv": case "wmv": case "mpg": case "m4v": case "3gp": return FileVideo;
    case "zip": case "tar": case "gz": case "bz2": case "rar": case "7z":
    case "xz": case "iso": case "jar": case "dmg": return FileZip;
    case "js": case "ts": case "tsx": case "json": case "py": case "html":
    case "css": case "yml": case "yaml": return FileCode;
    default: return File;
  }
}
