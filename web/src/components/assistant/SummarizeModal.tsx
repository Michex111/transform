import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, CopySimple, Sparkle } from "@phosphor-icons/react";
import type { AssistantSummaryResponse, FileMetadataResponse } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import { assistantErrorCopy } from "@/lib/assistantChat";

type SummaryState =
  | { status: "loading" }
  | { status: "ready"; data: AssistantSummaryResponse }
  | { status: "error"; code: string; message: string };

/**
 * "Summarize with AI" for one library file.
 *
 * The summary is produced on open and never cached: the file's contents can
 * change and the model is a live call, so showing a stale answer would be worse
 * than a moment of skeleton. A failure is recoverable in place (Retry) rather
 * than only by reopening the dialog.
 */
export function SummarizeModal({
  open,
  onClose,
  file,
}: {
  open: boolean;
  onClose: () => void;
  file: FileMetadataResponse | null;
}) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const navigate = useNavigate();

  const [state, setState] = useState<SummaryState>({ status: "loading" });
  const [copied, setCopied] = useState(false);
  // Bumped by Retry to re-run the request effect.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!open || !file) return;
    let cancelled = false;
    setState({ status: "loading" });
    setCopied(false);

    client
      .assistantSummarize(file.id)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const apiError = err as { code?: string; message?: string };
        setState({
          status: "error",
          code: apiError.code ?? "INTERNAL_ERROR",
          message: apiError.message ?? "Could not summarize that file.",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [open, file, attempt, client]);

  const summaryText =
    state.status === "ready"
      ? [state.data.summary, ...state.data.key_points.map((point) => `• ${point}`)]
          .filter(Boolean)
          .join("\n\n")
      : "";

  const copyToClipboard = useCallback(async () => {
    if (!summaryText) return;
    try {
      // `clipboard` is absent in insecure contexts; failing loudly is better
      // than a button that silently does nothing.
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(summaryText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toastError("Could not copy the summary.");
    }
  }, [summaryText, toastError]);

  function askFollowUp() {
    if (!file) return;
    const name = state.status === "ready" ? state.data.file_name || file.file_name : file.file_name;
    navigate("/app/assistant", {
      state: {
        prompt: `Tell me more about "${name}".`,
        fileId: file.id,
        fileName: name,
      },
    });
    onClose();
  }

  const errorCopy = state.status === "error" ? assistantErrorCopy(state.code, state.message) : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="AI summary"
      description={file?.file_name}
      maxWidth="max-w-lg"
    >
      <div className="space-y-4">
        {state.status === "loading" && (
          <div className="space-y-3" role="status" aria-label="Summarizing file">
            <p className="flex items-center gap-2 text-sm text-muted">
              <Sparkle size={15} weight="fill" className="text-primary" aria-hidden />
              Reading the file…
            </p>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        )}

        {state.status === "error" && errorCopy && (
          <div className="space-y-3">
            <div className="rounded-lg border border-outline bg-surface-variant/50 p-4">
              <p className="font-display text-sm font-semibold text-on-background">{errorCopy.title}</p>
              <p className="mt-1 text-sm text-muted">{errorCopy.detail}</p>
            </div>
            {errorCopy.kind !== "tier" && (
              <div className="flex justify-end">
                <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
                  Try again
                </Button>
              </div>
            )}
          </div>
        )}

        {state.status === "ready" && (
          <>
            <p className="whitespace-pre-wrap text-sm text-on-background">{state.data.summary}</p>
            {state.data.key_points.length > 0 && (
              <div>
                <h3 className="font-display text-sm font-semibold text-on-background">Key points</h3>
                <ul className="mt-2 space-y-1.5">
                  {state.data.key_points.map((point, index) => (
                    // Ordered model output with no stable identity of its own;
                    // the list is never reordered, so the index is stable.
                    <li key={index} className="flex gap-2 text-sm text-muted">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                      <span>{point}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {state.data.model && (
              <p className="font-mono text-[11px] text-muted">Model: {state.data.model}</p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={copyToClipboard} aria-live="polite">
                {copied ? <Check size={15} weight="bold" /> : <CopySimple size={15} />}
                {copied ? "Copied" : "Copy"}
              </Button>
              <Button onClick={askFollowUp}>
                <Sparkle size={15} weight="fill" /> Ask a follow-up
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
