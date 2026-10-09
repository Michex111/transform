import { useId, useState } from "react";
import { MagnifyingGlass, Plus, Trash, X } from "@phosphor-icons/react";
import type { AssistantConversation } from "@/api/types";
import { Button, Skeleton } from "@/components/ui";
import { filterConversations, groupConversations, relativeTimeLabel } from "@/lib/conversationGroups";

/**
 * The conversation rail: start a new chat, or reopen an old one.
 *
 * Each row is a select button plus a *sibling* delete button rather than a
 * nested one (a button inside a button is invalid HTML and does not behave on
 * touch). The delete control is only revealed on hover/focus, but it stays
 * focusable while transparent so a keyboard user can still reach it.
 */
export function ConversationList({
  conversations,
  activeId,
  loading,
  onSelect,
  onNew,
  onRequestDelete,
}: {
  conversations: AssistantConversation[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRequestDelete: (conversation: AssistantConversation) => void;
}) {
  const [query, setQuery] = useState("");
  // Unique, because the rail and the mobile drawer render this component at the
  // same time and a shared id would point both labels at one input.
  const searchId = useId();
  // Titles only. The list is fetched whole (no pagination on the endpoint), so
  // this filter is complete rather than partial — and nothing indexes message
  // bodies, which is why the placeholder says "titles" and claims nothing more.
  const matches = filterConversations(conversations, query);
  const groups = groupConversations(matches);
  const searching = query.trim().length > 0;

  // `min-w-0` on the root is load-bearing. As the flex item of the `w-64` rail
  // it inherits `min-width: auto`, which resolves to the min-content width of a
  // long, `truncate`d (nowrap) title — so the column refused to shrink below the
  // title, grew to hundreds of pixels, and the rail's `overflow-hidden` clipped
  // everything past 256px. `min-w-0` (with `w-full`, so it still fills the rail)
  // lets each row's `truncate` do its job.
  return (
    <div className="flex min-h-0 min-w-0 w-full flex-1 flex-col">
      <div className="shrink-0 p-3">
        <Button className="w-full" onClick={onNew}>
          <Plus size={16} weight="bold" /> New chat
        </Button>
        {/* Offered only once there is something to search, so an empty account
            is not greeted with a filter that can never match. */}
        {conversations.length > 0 && (
          <div className="relative mt-2">
            <MagnifyingGlass
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
            />
            <label htmlFor={`${searchId}-conversation-search`} className="sr-only">
              Search conversation titles
            </label>
            <input
              id={`${searchId}-conversation-search`}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search titles…"
              className="w-full rounded-lg border border-outline bg-surface-variant/40 py-1.5 pl-8 pr-8 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25"
            />
            {searching && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Clear conversation search"
                className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:min-w-11"
              >
                <X size={13} />
              </button>
            )}
          </div>
        )}
      </div>

      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          <div className="space-y-2 p-1">
            {[0, 1, 2, 3].map((row) => (
              <Skeleton key={row} className="h-12 w-full rounded-lg" />
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted">
            Your conversations will appear here.
          </p>
        ) : matches.length === 0 ? (
          // Distinct from "no conversations at all": the account has history,
          // this search just does not match any of it.
          <p className="px-3 py-6 text-center text-sm text-muted">
            No conversations match “{query.trim()}”.
          </p>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => (
              <section key={group.label} aria-label={group.label}>
                <h3 className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {group.label}
                </h3>
                <ul className="space-y-1">
                  {group.items.map((conversation) => {
                    const active = conversation.id === activeId;
                    const title = conversation.title || "Untitled chat";
                    const time = relativeTimeLabel(
                      conversation.updated_at ?? conversation.created_at,
                    );
                    return (
                      <li key={conversation.id} className="group relative">
                        <button
                          type="button"
                          onClick={() => onSelect(conversation.id)}
                          aria-current={active ? "true" : undefined}
                          className={`block w-full rounded-lg border-l-2 py-2 pl-3 pr-11 text-left transition-colors ${
                            active
                              ? "border-primary bg-primary-container text-on-primary-container"
                              : "border-transparent text-on-background hover:bg-surface-variant"
                          }`}
                        >
                          {/* Truncation must never be the only way to read a
                              title, and hover must never be the only way to
                              recover it: `title` carries the full name for a
                              pointer, and the text itself is always in the DOM
                              for a screen reader and for text selection. */}
                          <span className="block truncate text-sm font-medium" title={title}>
                            {title}
                          </span>
                          {time && (
                            <span
                              className={`block text-xs ${active ? "text-on-primary-container/70" : "text-muted"}`}
                            >
                              {time}
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => onRequestDelete(conversation)}
                          aria-label={`Delete conversation ${title}`}
                          className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-muted opacity-0 transition-opacity hover:bg-error/10 hover:text-error focus:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:opacity-100"
                        >
                          <Trash size={15} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </nav>
    </div>
  );
}
