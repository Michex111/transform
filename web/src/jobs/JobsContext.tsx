import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/api/client";
import type { ConversionJobResponse } from "@/api/types";

/** A client-side job with upload/progress augmentation. */
export interface UiJob extends ConversionJobResponse {
  fileName?: string;
  progress?: number;
  createdAt?: string;
  /** Error message from the SSE stream for failed conversions. */
  errorMessage?: string;
}

const STORAGE_KEY = "transform_jobs";

interface JobsContextValue {
  jobs: UiJob[];
  addJob: (job: UiJob) => void;
  updateJob: (jobId: string, patch: Partial<UiJob>) => void;
  removeJob: (jobId: string) => void;
  refresh: (range?: string) => Promise<void>;
}

const JobsContext = createContext<JobsContextValue | undefined>(undefined);

export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<UiJob[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as UiJob[];
    } catch {
      return [];
    }
  });
  const subs = useRef<Map<string, () => void>>(new Map());

  // Persist to localStorage whenever jobs change.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs));
    } catch {
      /* ignore quota */
    }
  }, [jobs]);

  // Stable primitive key derived from the *set* of active job ids, so the
  // subscription effect only re-runs when the active set changes — not on every
  // SSE progress tick (which mutates `jobs` but not the active set).
  const activeJobIds = jobs
    .filter((j) => j.status === "PROCESSING" || j.status === "PENDING")
    .map((j) => j.job_id);
  const activeKey = activeJobIds.slice().sort().join("\u0001");

  // Keep terminal-state jobs subscribed for live progress; drop subscriptions when done.
  useEffect(() => {
    const ids = activeKey ? activeKey.split("\u0001") : [];
    for (const id of ids) {
      if (subs.current.has(id)) continue;
      const cleanup = api.subscribeToJob(id, {
        onProgress: (evt) => {
          setJobs((prev) =>
            prev.map((j) =>
              j.job_id === id
                ? {
                    ...j,
                    status: evt.status,
                    progress: evt.progress,
                    // `message` carries status prose ("downloading file", the
                    // failure reason, …). It must never overwrite the user's
                    // real filename (which is what the History/Queue file
                    // column renders via `fileName ?? input_file`). For FAILED
                    // events, surface the message as the error text so the
                    // error tooltip is populated without waiting for a refresh.
                    errorMessage:
                      evt.status === "FAILED"
                        ? evt.message ?? j.errorMessage
                        : j.errorMessage,
                  }
                : j,
            ),
          );
        },
        onError: (msg) => {
          setJobs((prev) =>
            prev.map((j) =>
              j.job_id === id
                ? { ...j, status: "FAILED", errorMessage: msg || "Conversion failed" }
                : j,
            ),
          );
        },
        onDone: () => {
          subs.current.delete(id);
        },
      });
      subs.current.set(id, cleanup);
    }
    // No teardown here: subscriptions self-terminate via `onDone` (the server
    // closes the stream), and the provider only unmounts at the app level.
    // Keying on `activeKey` means SSE ticks (which change `jobs` but not the
    // active set) no longer tear down/recreate every subscription.
  }, [activeKey]);

  const addJob = useCallback((job: UiJob) => {
    setJobs((prev) => [{ ...job, createdAt: job.createdAt ?? new Date().toISOString() }, ...prev]);
  }, []);

  const updateJob = useCallback((jobId: string, patch: Partial<UiJob>) => {
    setJobs((prev) => prev.map((j) => (j.job_id === jobId ? { ...j, ...patch } : j)));
  }, []);

  const removeJob = useCallback((jobId: string) => {
    subs.current.get(jobId)?.();
    subs.current.delete(jobId);
    setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
  }, []);

  const refresh = useCallback(async (range?: string) => {
    try {
      const { jobs: serverJobs } = await api.conversionHistory(1, 100, range);
      // Merge server history with any local in-flight job progress, so active
      // jobs keep their progress/errorMessage until the server catches up.
      setJobs((prev) => {
        const localActive = prev.filter(
          (j) => j.status === "PENDING" || j.status === "PROCESSING" || j.status === "AWAITING_UPLOAD",
        );
        const merged = [...localActive];
        for (const s of serverJobs) {
          const existing = localActive.find((l) => l.job_id === s.job_id);
          merged.push(
            existing
              ? { ...existing, ...s, progress: existing.progress }
              : { ...s, errorMessage: s.error_message ?? undefined },
          );
        }
        // Deduplicate by job_id, newest-first as returned by the server.
        const seen = new Set<string>();
        return merged.filter((j) => {
          if (seen.has(j.job_id)) return false;
          seen.add(j.job_id);
          return true;
        });
      });
    } catch {
      // History endpoint unavailable — keep current local state.
      setJobs((prev) => [...prev]);
    }
  }, []);

  return (
    <JobsContext.Provider value={{ jobs, addJob, updateJob, removeJob, refresh }}>
      {children}
    </JobsContext.Provider>
  );
}

export function useJobs() {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs must be used within JobsProvider");
  return ctx;
}
