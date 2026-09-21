/**
 * Pure retention rules for the guest (no-account) conversion history.
 *
 * Kept apart from the `useGuestHistory` hook so the rules that decide what a
 * device retains can be unit-tested without a browser. Guest history lives only
 * in `localStorage`, and it used to grow without bound — one entry per
 * conversion, forever — until it filled the ~5 MB quota, after which every
 * write threw and was swallowed: the history silently stopped persisting.
 */

import type { GuestHistoryItem } from "@/api/types";
import { isActiveJob } from "@/jobs/jobStore";

/** Most terminal entries retained on a device. */
export const MAX_GUEST_HISTORY = 50;

/**
 * Trim the guest history to a bounded size, preserving newest-first order.
 *
 * The newest `max` *terminal* entries are kept. An in-flight entry is never
 * evicted, no matter how old: dropping one would tear down its SSE
 * subscription and strand a conversion the user is watching.
 */
export function capGuestHistory(
  items: readonly GuestHistoryItem[],
  max: number = MAX_GUEST_HISTORY,
): GuestHistoryItem[] {
  if (items.length <= max) return [...items];

  const kept: GuestHistoryItem[] = [];
  let terminalKept = 0;
  for (const item of items) {
    if (isActiveJob(item)) {
      kept.push(item);
      continue;
    }
    if (terminalKept < max) {
      kept.push(item);
      terminalKept += 1;
    }
  }
  return kept;
}
