// Drive search behind the composer's `@` document picker.
//
// The picker must not pull the whole drive into the browser, so every keystroke
// is a server search — which makes two things load-bearing:
//
//   * **debounce**, so typing a name does not fire a request per character; and
//   * **cancellation**, so a slow response for `@re` cannot land after a fast one
//     for `@resume` and replace the correct list with a stale one. Results are
//     ordered by arrival, not by request, so this is a real race rather than a
//     theoretical one on a slow connection.
//
// Kept out of the component so the composer stays about composing, and so the
// debounce and supersede rules can be reasoned about (and tested) on their own.

import { useEffect, useRef, useState } from "react";
import type { FileMetadataResponse } from "@/api/types";
import type { ApiClient } from "@/api/client";

/** How long typing must pause before a search is sent. */
export const MENTION_DEBOUNCE_MS = 180;

export interface DocumentMentionState {
  results: FileMetadataResponse[];
  loading: boolean;
  /** The server's message, when the search itself failed (never a fake result). */
  error: string | null;
  /** Index of the highlighted row; always within `results`. */
  activeIndex: number;
  /** The highlighted file, or `null` when there is nothing to choose. */
  activeResult: FileMetadataResponse | null;
  /** Move the highlight by `delta`, wrapping at both ends. No-op when empty. */
  moveActive: (delta: number) => void;
  /** Highlight a row directly (pointer hover). */
  setActiveIndex: (index: number) => void;
}

/**
 * Search the caller's drive for the picker's current query.
 *
 * A blank query does not search: the API answers an empty `q` with nothing, and
 * a picker that listed the whole drive on `@` would be both slow and useless.
 * The caller is expected to show its own empty-state prompt for that case.
 */
export function useDocumentMention({
  client,
  query,
  enabled,
  debounceMs = MENTION_DEBOUNCE_MS,
}: {
  client: Pick<ApiClient, "searchFiles">;
  /** The text typed after the `@`. */
  query: string;
  /** False while no mention is open, which stops any request in flight. */
  enabled: boolean;
  debounceMs?: number;
}): DocumentMentionState {
  const [results, setResults] = useState<FileMetadataResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndexState] = useState(0);
  // Increments on every effect run, so a response can tell whether the query it
  // answers is still the current one.
  const requestRef = useRef(0);

  const trimmed = query.trim();

  useEffect(() => {
    // Closing the picker invalidates whatever is in flight; otherwise a response
    // could repopulate a list the user has already dismissed.
    if (!enabled) {
      requestRef.current += 1;
      setLoading(false);
      return;
    }

    const requestId = ++requestRef.current;

    if (!trimmed) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);

    const timer = window.setTimeout(() => {
      client
        .searchFiles(trimmed)
        .then((response) => {
          if (requestId !== requestRef.current) return;
          setResults(response.files);
          setActiveIndexState(0);
        })
        .catch((err: unknown) => {
          if (requestId !== requestRef.current) return;
          // Drop any previous results rather than showing a stale list next to
          // an error: the two together read as "these are the matches, and also
          // something went wrong".
          setResults([]);
          setError(err instanceof Error ? err.message : "Could not search your files.");
        })
        .finally(() => {
          if (requestId === requestRef.current) setLoading(false);
        });
    }, debounceMs);

    // Cancels the pending timer. A request already on the wire is handled by the
    // id check above, not by this.
    return () => window.clearTimeout(timer);
  }, [client, trimmed, enabled, debounceMs]);

  const count = results.length;
  const safeIndex = count === 0 ? 0 : Math.min(activeIndex, count - 1);

  return {
    results,
    loading,
    error,
    activeIndex: safeIndex,
    activeResult: results[safeIndex] ?? null,
    moveActive: (delta: number) => {
      if (count === 0) return;
      // Wrap, so ArrowUp from the top lands on the last row rather than nowhere.
      setActiveIndexState((current) => {
        const from = Math.min(current, count - 1);
        return (from + delta + count) % count;
      });
    },
    setActiveIndex: (index: number) => {
      if (count === 0) return;
      setActiveIndexState(Math.max(0, Math.min(index, count - 1)));
    },
  };
}
