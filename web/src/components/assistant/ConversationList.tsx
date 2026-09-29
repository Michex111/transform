import { Plus, Trash } from "@phosphor-icons/react";
import type { AssistantConversation } from "@/api/types";
import { Button, Skeleton } from "@/components/ui";
import { groupConversations, relativeTimeLabel } from "@/lib/conversationGroups";

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
  const groups = groupConversations(conversations);

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
                          <span className="block truncate text-sm font-medium">{title}</span>
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
