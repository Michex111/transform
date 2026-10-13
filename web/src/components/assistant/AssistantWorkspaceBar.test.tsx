import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AssistantWorkspaceBar } from "@/components/assistant/AssistantWorkspaceBar";

/**
 * The assistant's workspace selector.
 *
 * Rendered with `renderToString` because this project's vitest environment is
 * `node` (no jsdom) — so these assertions cover the markup a user gets on first
 * paint: the control exists, it is labelled, and it says what the assistant will
 * do when nothing is chosen. The list itself arrives in an effect, which a server
 * render does not run, and that limit is stated rather than worked around.
 */

const listWorkspaces = vi.fn();

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ api: { listWorkspaces } }),
}));

// Imported at module scope, not inside the first test. A dynamic `await import()`
// put the entire module-transform cost inside that one test's 5s budget, which
// made it time out roughly half the time regardless of what it asserted.
// `vi.mock` is hoisted above imports, so the mock still applies.
function renderBar(workspaceId: string | null = null) {
  return renderToString(
    <MemoryRouter>
      <AssistantWorkspaceBar workspaceId={workspaceId} onChange={() => {}} />
    </MemoryRouter>,
  );
}

describe("AssistantWorkspaceBar", () => {
  it("renders a labelled workspace control", () => {
    const html = renderBar(null);
    // The control is the app's `Dropdown`, so its accessible name comes from
    // `aria-label` rather than a `<label for>` — a native `<select>` here was the
    // one surface in the product that ignored the theme entirely (an OS-drawn
    // menu knows nothing about `--color-surface` or the 8px radius).
    expect(html).toContain('aria-label="Workspace"');
    expect(html).toContain('aria-haspopup="listbox"');
    expect(html).toContain("Workspace");
  });

  it("offers an explicit 'no workspace' choice", () => {
    const html = renderBar(null);
    // The default is the honest one: the assistant asks rather than picking a
    // workspace for the user. The label is on the trigger, so it is readable
    // before the popup is ever opened.
    expect(html).toContain("None — ask me first");
  });

  it("ties the 'will ask' hint to the control for a screen reader", () => {
    const html = renderBar(null);
    // The hint explains what choosing "None" does, so it must reach the element
    // that takes focus rather than sit beside it unannounced.
    expect(html).toContain('aria-describedby="assistant-workspace-hint"');
    expect(html).toContain('id="assistant-workspace-hint"');
  });

  it("says what happens when nothing is selected", () => {
    const html = renderBar(null);
    expect(html).toContain("ask which workspace to use");
  });

  it("does not show the 'will ask' hint once a workspace is chosen", () => {
    const html = renderBar("ws-1");
    expect(html).not.toContain("ask which workspace to use");
  });

  it("does not fetch anything during a server render", () => {
    listWorkspaces.mockClear();
    renderBar(null);
    // A render must not fire a request: the list is loaded in an effect, which
    // is what keeps this component safe in a server render and in a test.
    expect(listWorkspaces).not.toHaveBeenCalled();
  });
});
