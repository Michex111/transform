import { useCallback, useEffect, useRef, useState } from "react";
import type { GuestHistoryItem } from "@/api/types";
import { capGuestHistory } from "@/lib/guestHistory";
import { isActiveJob } from "@/jobs/jobStore";

const STORAGE_KEY = "transform_guest_jobs";

/** Read the guest history array from localStorage, tolerating missing/corrupt data. */
function readHistory(): GuestHistoryItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as GuestHistoryItem[]) : [];
  } catch {
    return [];
  }
}

export interface GuestSubscribeHandlers {
  onProgress: (evt: import("@/api/types").JobProgressEvent) => void
  onError: (msg: string) => void
  onDone: () => void
}

export interface GuestHistorySubscribe {
  (item: GuestHistoryItem, handlers: GuestSubscribeHandlers): () => void
  refresh: (item: GuestHistoryItem) => Promise<Partial<GuestHistoryItem> | null>
}

/**
 * Client-side, self-contained guest conversion history.
 *
 * Lives entirely on the page — it does NOT touch JobsContext or the auth
 * store. Guests get an equivalent of "history" that persists across refresh
 * via localStorage under `transform_guest_jobs`.
 *
 * The hook also owns the live SSE subscriptions for in-progress jobs so that
 * removing or clearing history cleanly aborts the matching streams. `subscribe`
 * is provided by the caller (the page wires it to `api.guestSubscribeToJob`).
 */
export function useGuestHistory(subscribe: GuestHistorySubscribe) {
  const [items, setItems] = useState<GuestHistoryItem[]>(() =>
    // Re-apply the cap on read: an older session may have written an unbounded
    // list, and it must not be adopted as-is.
    capGuestHistory(readHistory()),
  );
  const subs = useRef<Map<string, () => void>>(new Map());

  // Persist any change back to localStorage.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch {
      /* ignore quota */
    }
  }, [items]);

  // Stable primitive key derived from the *set* of active job ids, so the
  // subscription effect only re-runs when the active set changes — not on
  // every SSE progress tick (which mutates `items` but not the active set).
  // `isActiveJob` is the one shared predicate (it counts AWAITING_UPLOAD too).
  const activeIds = items.filter(isActiveJob).map((i) => i.job_id);
  const activeKey = activeIds.slice().sort().join("\u0001");

  // Subscribe to live progress for currently-active jobs and clean up any
  // subscriptions whose job is no longer present (removed/cleared).
  useEffect(() => {
    const ids = activeKey ? activeKey.split("\u0001") : [];
    const present = new Set(ids);

    // Teardown subscriptions for jobs that are no longer active/present.
    for (const [id, cleanup] of subs.current) {
      if (!present.has(id)) {
        cleanup();
        subs.current.delete(id);
      }
    }

    for (const id of ids) {
      if (subs.current.has(id)) continue;
      const item = items.find((i) => i.job_id === id);
      if (!item) continue;
      const cleanup = subscribe(
        item,
        {
          onProgress: (evt) => updateItem(id, {
            status: evt.status,
            progress: evt.progress,
            errorMessage: evt.status === "FAILED" ? evt.message ?? undefined : undefined,
          }),
          onError: (msg) => updateItem(id, { status: "FAILED", errorMessage: msg || "Conversion failed" }),
          onDone: async () => {
            try {
              // Refresh the job to capture the final status + download URL.
              const patch = await subscribe.refresh(item);
              if (patch) updateItem(id, patch);
            } catch {
              /* ignore refresh on close */
            } finally {
              subs.current.delete(id);
            }
          },
        },
      );
      subs.current.set(id, cleanup);
    }
    // The caller's `subscribe` and `updateItem` are stable; `items` is only read
    // to find the active item (each job's guest token is immutable).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey]);

  const addItem = useCallback((item: GuestHistoryItem) => {
    // Bounded so the localStorage quota can never be exhausted by history
    // alone (which used to stop persistence entirely, silently).
    setItems((prev) => capGuestHistory([item, ...prev]));
  }, []);

  const updateItem = useCallback((jobId: string, patch: Partial<GuestHistoryItem>) => {
    setItems((prev) => prev.map((i) => (i.job_id === jobId ? { ...i, ...patch } : i)));
  }, []);

  const removeItem = useCallback((jobId: string) => {
    subs.current.get(jobId)?.();
    subs.current.delete(jobId);
    setItems((prev) => prev.filter((i) => i.job_id !== jobId));
  }, []);

  const clearHistory = useCallback(() => {
    for (const cleanup of subs.current.values()) cleanup();
    subs.current.clear();
    setItems([]);
  }, []);

  return { items, addItem, updateItem, removeItem, clearHistory };
}

export type GuestHistoryApi = ReturnType<typeof useGuestHistory>;
