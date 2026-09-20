import { formatExt } from "@/lib/format";
import { formatTint, formatVisual } from "@/lib/formatVisual";

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
  const { Icon, color, label } = formatVisual(ext);
  const isImage = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "avif"].includes(ext);

  if (isImage && url) {
    return (
      <div className={`relative w-full overflow-hidden rounded-t-xl bg-surface-variant ${className}`}>
        <img src={url} alt={fileName} className="h-full w-full object-contain" />
      </div>
    );
  }

  return (
    <div
      className={`relative flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-t-xl ${className}`}
      style={{
        background: `linear-gradient(160deg, ${formatTint(color, 13)}, ${formatTint(color, 3)})`,
      }}
    >
      <Icon size={40} weight="duotone" aria-hidden style={{ color }} />
      <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase"
        style={{ color, borderColor: formatTint(color, 27), background: formatTint(color, 10) }}>
        {label}
      </span>
    </div>
  );
}
