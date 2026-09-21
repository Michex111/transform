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
import { useAuth } from "@/auth/AuthContext";
import {
  LEGACY_JOBS_STORAGE_KEY,
  readStoredJobs,
  reconcileJobs,
  removeStoredJobs,
  storageKeyFor,
  type UiJob,
} from "@/jobs/jobStore";

// Re-exported for the pages that render jobs (`HistoryPage`, `QueuePage`).
export type { UiJob };

interface JobsContextValue {
  jobs: UiJob[];
  addJob: (job: UiJob) => void;
  updateJob: (jobId: string, patch: Partial<UiJob>) => void;
  removeJob: (jobId: string) => void;
  refresh: (range?: string) => Promise<void>;
}

const JobsContext = createContext<JobsContextValue | undefined>(undefined);

/**
 * Provides the client-side conversion job list (queue + history).
 *
 * The store is scoped to the signed-in identity and is remounted whenever that
 * identity changes, so one account's conversions can never appear in another's
 * session. It previously read and wrote a single unscoped `localStorage` entry
 * that was never cleared on sign-out, so creating a brand-new account in the
 * same browser opened onto the previous account's queue and history.
 */
export function JobsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const previousId = useRef<number | null | undefined>(undefined);

  // One-time migration: drop the unscoped cache instead of adopting it.
  useEffect(() => {
    try {
      localStorage.removeItem(LEGACY_JOBS_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  // Once an identity is gone, discard its cache so a shared browser does not
  // retain one account's history for the next one to sign in.
  useEffect(() => {
    const previous = previousId.current;
    previousId.current = userId;
    if (previous === undefined || previous === userId) return;
    if (previous !== null) removeStoredJobs(previous, localStorage);
  }, [userId]);

  return (
    <JobsStore key={userId ?? "anon"} userId={userId}>
      {children}
    </JobsStore>
  );
}

function JobsStore({ userId, children }: { userId: number | null; children: ReactNode }) {
  const [jobs, setJobs] = useState<UiJob[]>(() => readStoredJobs(userId, localStorage));
  const subs = useRef<Map<string, () => void>>(new Map());
  // Jobs started during this session. Only these may outlive a server refresh
  // while still in flight; anything else on screen is stale by definition.
  const sessionJobIds = useRef<Set<string>>(new Set());

  // Persist to localStorage whenever jobs change.
  useEffect(() => {
    try {
      localStorage.setItem(storageKeyFor(userId), JSON.stringify(jobs));
    } catch {
      /* ignore quota */
    }
  }, [jobs, userId]);

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
    // No teardown *here*: subscriptions self-terminate via `onDone` (the server
    // closes the stream). Keying on `activeKey` means SSE ticks (which change
    // `jobs` but not the active set) no longer tear down/recreate every
    // subscription. Unmount teardown is handled by the effect below.
  }, [activeKey]);

  // This store is remounted whenever the signed-in identity changes, so it can
  // unmount while streams are still open. Close them, otherwise the previous
  // account's subscriptions linger and keep pushing their events into a store
  // that no longer belongs to them.
  useEffect(() => {
    const openSubscriptions = subs.current;
    return () => {
      for (const cleanup of openSubscriptions.values()) cleanup();
      openSubscriptions.clear();
    };
  }, []);

  const addJob = useCallback((job: UiJob) => {
    sessionJobIds.current.add(job.job_id);
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
      // The server is authoritative for this identity: reconcile against it so
      // another account's leftovers cannot survive a refresh. See `reconcileJobs`.
      setJobs((prev) => reconcileJobs(prev, serverJobs, sessionJobIds.current));
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
