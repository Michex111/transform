// The conversation rail.
//
// Two things are pinned here, both of which a screenshot fails to reveal:
// a truncated title must stay recoverable (the tooltip carries the full name),
// and JSX comments must never leak into the rendered output. The second is a
// real regression that shipped briefly: a `//` comment placed between JSX
// children — rather than inside `{/* … */}` — is literal text to the parser, so
// it rendered as a paragraph of source code above the conversation title.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { AssistantConversation } from "@/api/types";
import { ConversationList } from "@/components/assistant/ConversationList";

const CONVERSATION: AssistantConversation = {
  id: "conv-1",
  title: "What files did I add recently?",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function render(conversations: AssistantConversation[] = [CONVERSATION]): string {
  return renderToString(
    <ConversationList
      conversations={conversations}
      activeId={null}
      loading={false}
      onSelect={() => {}}
      onNew={() => {}}
      onRequestDelete={() => {}}
    />,
  );
}

describe("ConversationList", () => {
  it("renders the conversation title", () => {
    expect(render()).toContain("What files did I add recently?");
  });

  it("carries the full title in a tooltip, so truncation is never the only way to read it", () => {
    const html = render();
    expect(html).toContain('title="What files did I add recently?"');
  });

  it("never leaks source comments into the DOM", () => {
    // `//` between JSX children is text, not a comment — the regression this
    // guards against. Assert on the comment's own words rather than on "//",
    // which also appears in every icon's `xmlns="http://www.w3.org/2000/svg"`.
    const html = render();
    expect(html).not.toContain("must never be the only way");
    expect(html).not.toContain("for a screen reader and for text selection");
  });

  it("points the delete control at the conversation it will remove", () => {
    const html = render();
    expect(html).toContain(
      'aria-label="Delete conversation What files did I add recently?"',
    );
  });

  it("guides an empty rail instead of leaving a blank panel", () => {
    expect(render([])).toContain("Your conversations will appear here.");
  });
});
