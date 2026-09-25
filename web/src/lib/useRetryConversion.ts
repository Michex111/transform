// The shared "retry a failed conversion" flow.
//
// Two pages offer it — a failed row on the Convert page and the History row —
// and both need the same three-way decision: is the input object still in
// storage (re-enqueue server-side), is the original file still cached in this
// tab (re-upload it), or is there nothing left to retry (send the user to
// Convert with the formats pre-filled rather than leaving them at a dead end).
//
// It lives here rather than in either page because the third branch is the one
// nobody exercises by hand, and it is exactly the branch that would rot if the
// two copies were allowed to drift.

import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { dropCachedFile, getCachedFile } from "@/lib/fileCache";

export interface RetryConversion {
  /** The job currently being re-checked/re-queued, or null when idle. */
  retryingId: string | null;
  retry: (job: UiJob) => Promise<void>;
}

export function useRetryConversion(): RetryConversion {
  const { api: client } = useAuth();
  const { updateJob } = useJobs();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const retry = useCallback(
    async (job: UiJob) => {
      if (retryingId !== null) return;
      setRetryingId(job.job_id);
      try {
        // 1. Is the input still in object storage? If not, a plain re-enqueue
        //    would fail again for the same reason, so the file has to come back
        //    first.
        const exists = await client.objectExists(job.object_key || job.input_file);

        if (!exists) {
          // 1a. This tab may still hold the file it uploaded. Re-running the
          //     full conversion route re-encrypts and re-uploads it, and the
          //     resulting job replaces the failed one in place so the row the
          //     user is looking at becomes the new attempt.
          const cached = getCachedFile(job.job_id);
          if (cached) {
            try {
              const newJob = await client.convertWithFile(
                cached.source,
                cached.target,
                cached.file,
              );
              updateJob(job.job_id, {
                ...newJob,
                fileName: cached.file.name,
                status: "PENDING",
                progress: 0,
                createdAt: new Date().toISOString(),
              });
              dropCachedFile(job.job_id);
              success("Input file was missing — re-uploaded and re-queued.");
            } catch (err) {
              error(err instanceof Error ? err.message : "Could not re-upload file");
            }
            return;
          }

          // 1b. Nothing to re-upload with. Pre-fill Convert with the formats so
          //     re-selecting the file is one step, and say why. Navigating is
          //     deliberate: leaving the user on a row whose retry cannot work
          //     would be the dead end.
          navigate("/app/convert", {
            state: { source: job.source_format, target: job.target_format },
          });
          error("The input file is no longer in storage. Re-select it to convert.");
          return;
        }

        // 2. The input still exists — re-enqueue server-side, no re-upload.
        const updated = await client.retryJob(job.job_id);
        updateJob(job.job_id, {
          status: "PENDING",
          progress: 0,
          output_file: updated.output_file,
          download_url: updated.download_url,
          // The previous attempt's failure no longer describes this job, so the
          // row must not keep showing it while the retry runs.
          errorMessage: undefined,
          finishedAt: undefined,
        });
        success("Conversion re-queued — tracking it now.");
      } catch (err) {
        error(err instanceof Error ? err.message : "Could not retry conversion");
      } finally {
        setRetryingId(null);
      }
    },
    [client, error, navigate, retryingId, success, updateJob],
  );

  return { retryingId, retry };
}
