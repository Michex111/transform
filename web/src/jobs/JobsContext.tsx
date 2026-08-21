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
}

const STORAGE_KEY = "transform_jobs";

interface JobsContextValue {
  jobs: UiJob[];
  addJob: (job: UiJob) => void;
  updateJob: (jobId: string, patch: Partial<UiJob>) => void;
  removeJob: (jobId: string) => void;
  refresh: () => Promise<void>;
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

  // Keep terminal-state jobs subscribed for live progress; drop subscriptions when done.
  useEffect(() => {
    for (const job of jobs) {
      const active = job.status === "PROCESSING" || job.status === "PENDING";
      if (active && !subs.current.has(job.job_id)) {
        const cleanup = api.subscribeToJob(job.job_id, {
          onProgress: (evt) => {
            setJobs((prev) =>
              prev.map((j) =>
                j.job_id === job.job_id
                  ? { ...j, status: evt.status, progress: evt.progress, fileName: evt.message ?? j.fileName }
                  : j,
              ),
            );
          },
          onError: () => {
            setJobs((prev) =>
              prev.map((j) => (j.job_id === job.job_id ? { ...j, status: "FAILED" } : j)),
            );
          },
          onDone: () => {
            subs.current.delete(job.job_id);
          },
        });
        subs.current.set(job.job_id, cleanup);
      }
    }
    return () => {
      // On unmount, clean up all subscriptions.
      // Note: only the provider unmounts at app level, so this is safe.
    };
  }, [jobs]);

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

  const refresh = useCallback(async () => {
    // There is no job-list API, so refresh is a no-op that re-renders.
    setJobs((prev) => [...prev]);
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
