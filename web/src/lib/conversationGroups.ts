// Conversation rail grouping + relative timestamps.
//
// The rail reads as an editorial list (Today / Previous 7 days / Earlier) with
// a relative time per row, rather than an undifferentiated chat menu. Kept pure
// and clock-injected so the grouping rules can be tested without freezing the
// real clock.

import type { AssistantConversation } from "@/api/types";

/** The three buckets the rail renders, in display order. */
export const CONVERSATION_BUCKETS = ["Today", "Previous 7 days", "Earlier"] as const;
export type ConversationBucket = (typeof CONVERSATION_BUCKETS)[number];

export interface ConversationGroup {
  label: ConversationBucket;
  items: AssistantConversation[];
}

/** The instant a conversation should be bucketed by. */
function conversationTimestamp(conversation: AssistantConversation): number {
  const raw = conversation.updated_at ?? conversation.created_at;
  const time = Date.parse(raw);
  return Number.isNaN(time) ? 0 : time;
}

/** Calendar day index (local) for an instant, so "today" respects the viewer's timezone. */
function dayIndex(ms: number, now: number): number {
  const startOfDay = (value: number) => {
    const d = new Date(value);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  };
  return Math.round((startOfDay(now) - startOfDay(ms)) / 86_400_000);
}

/**
 * Bucket conversations and sort each bucket newest-first.
 *
 * "Today" is the viewer's local calendar day, not the last 24 hours: a
 * conversation from 11pm yesterday belongs in "Previous 7 days" even though it
 * is only a few hours old, because that is what the labels promise.
 */
export function groupConversations(
  conversations: readonly AssistantConversation[],
  now: number = Date.now(),
): ConversationGroup[] {
  const buckets: Record<ConversationBucket, AssistantConversation[]> = {
    Today: [],
    "Previous 7 days": [],
    Earlier: [],
  };

  for (const conversation of conversations) {
    const days = dayIndex(conversationTimestamp(conversation), now);
    if (days <= 0) buckets.Today.push(conversation);
    else if (days <= 7) buckets["Previous 7 days"].push(conversation);
    else buckets.Earlier.push(conversation);
  }

  for (const bucket of CONVERSATION_BUCKETS) {
    buckets[bucket].sort(
      (a, b) => conversationTimestamp(b) - conversationTimestamp(a),
    );
  }

  return CONVERSATION_BUCKETS.filter((label) => buckets[label].length > 0).map((label) => ({
    label,
    items: buckets[label],
  }));
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

/**
 * A short, relative timestamp for a rail row.
 *
 * Deliberately finite: "Just now", "12m ago", "3h ago", "Yesterday", "4d ago",
 * then an absolute date. An unparseable or absent timestamp renders as an empty
 * string so the row simply omits the line instead of inventing a time.
 */
export function relativeTimeLabel(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return "";
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return "";

  const elapsed = now - time;
  // A slightly future timestamp (clock skew between client and server) reads as
  // "now" rather than a negative duration.
  if (elapsed < MINUTE) return "Just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m ago`;

  const days = dayIndex(time, now);
  if (days <= 0) return `${Math.floor(elapsed / HOUR)}h ago`;
  if (days === 1) return "Yesterday";
  if (days <= 7) return `${days}d ago`;

  return new Date(time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(new Date(time).getFullYear() === new Date(now).getFullYear() ? {} : { year: "numeric" }),
  });
}

/**
 * The conversations whose title contains `query`, case-insensitively.
 *
 * Titles only, and it is a client-side filter on purpose: the conversation list
 * is fetched whole (the API returns the caller's conversations in one response
 * with no pagination), so filtering it in the browser is complete rather than
 * partial. The alternative — a search endpoint — would have to be built, and
 * would not find anything this does not. Message *content* is NOT searched:
 * nothing indexes it, so the placeholder says "titles" and the empty state does
 * not promise more than it delivers.
 *
 * A blank query returns the list unchanged (identity, not a copy): an empty
 * search box means "no filter", not "no results".
 */
export function filterConversations(
  conversations: readonly AssistantConversation[],
  query: string,
): readonly AssistantConversation[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return conversations;
  return conversations.filter((conversation) =>
    (conversation.title ?? "").toLowerCase().includes(needle),
  );
}

