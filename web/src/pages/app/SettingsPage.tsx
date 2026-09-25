import { useCallback, useRef, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Card, SkeletonText } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";
import { ApiKeysSection } from "./settings/ApiKeysSection";
import { DangerZoneSection } from "./settings/DangerZoneSection";
import { DriveSection } from "./settings/DriveSection";
import { PasswordSection } from "./settings/PasswordSection";
import { PhoneSection } from "./settings/PhoneSection";
import { ProfileSection } from "./settings/ProfileSection";
import { SETTINGS_TABS, readSettingsTab, type SettingsTab } from "./settings/settingsTabs";

const TAB_LABELS: Record<SettingsTab, string> = {
  profile: "Profile",
  phone: "Phone",
  "api-keys": "API keys",
  danger: "Danger zone",
};

function tabId(tab: SettingsTab) {
  return `settings-tab-${tab}`;
}

function panelId(tab: SettingsTab) {
  return `settings-panel-${tab}`;
}

function SettingsPanel({ tab }: { tab: SettingsTab }) {
  switch (tab) {
    case "profile":
      // Three cards, all always visible: the password form and the drive default
      // are standalone cards beside the profile rather than tabs of their own,
      // so changing a password (or where files are saved) never hides the rest
      // of the account. `space-y-6` is the page's own card rhythm.
      return (
        <div className="space-y-6">
          <ProfileSection />
          <PasswordSection />
          <DriveSection />
        </div>
      );
    case "phone":
      return <PhoneSection />;
    case "api-keys":
      return <ApiKeysSection />;
    case "danger":
      return <DangerZoneSection />;
  }
}

/**
 * The account settings page: four sections over one URL, with the standalone
 * password and drive cards always visible under Profile.
 *
 * The active section is the `?tab=` parameter rather than local state, because
 * the shell's profile dropdown links straight into a section — a link has to
 * work on a cold load. Switching tabs uses `replace`, so the back button leaves
 * the page instead of walking the user backwards through tabs they just
 * visited. `?tab=password` is a retired tab that resolves to `profile`; see
 * `settings/settingsTabs.ts`.
 */
export function SettingsPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const activeTab = readSettingsTab(params.toString());

  // Roving tabindex: only the selected tab is in the tab order, and focus moves
  // with the arrow keys. Refs hold the DOM nodes so a key press can move *real*
  // focus, which is what makes a screen reader announce the tab that was just
  // selected.
  const tabRefs = useRef<Partial<Record<SettingsTab, HTMLButtonElement | null>>>({});

  const selectTab = useCallback(
    (tab: SettingsTab) => {
      setParams(tab === "profile" ? {} : { tab }, { replace: true });
    },
    [setParams],
  );

  function onTabListKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const current = SETTINGS_TABS.indexOf(activeTab);
    let next: number;
    switch (e.key) {
      case "ArrowRight":
        next = (current + 1) % SETTINGS_TABS.length;
        break;
      case "ArrowLeft":
        next = (current - 1 + SETTINGS_TABS.length) % SETTINGS_TABS.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = SETTINGS_TABS.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    const tab = SETTINGS_TABS[next];
    // Automatic activation: moving focus selects the tab. Deliberate here,
    // because every panel is local state and free to render — there is no
    // expensive fetch to defer behind an Enter press.
    selectTab(tab);
    tabRefs.current[tab]?.focus();
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="font-display text-2xl font-semibold">Settings</h1>

      {/* One row, scrollable on a phone. The negative margin plus matching
          padding lets the strip reach the viewport edges (the shell pads the
          content by 16px) without ever widening the page: the strip scrolls
          itself rather than pushing four tabs into the layout. */}
      <div
        role="tablist"
        aria-label="Account settings"
        onKeyDown={onTabListKeyDown}
        className="-mx-4 flex gap-1 overflow-x-auto border-b border-outline px-4 sm:mx-0 sm:px-0"
      >
        {SETTINGS_TABS.map((tab) => {
          const selected = tab === activeTab;
          const tabRef = (el: HTMLButtonElement | null) => {
            tabRefs.current[tab] = el;
          };
          return (
            <button
              key={tab}
              id={tabId(tab)}
              ref={tabRef}
              type="button"
              role="tab"
              aria-selected={selected}
              // Points at the panel this tab opens. Only the selected panel is
              // mounted (each one is local state, and mounting all four would
              // fetch API keys behind the user's back), so the relationship
              // resolves for whichever tab is current.
              aria-controls={panelId(tab)}
              // Roving tabindex: -1 keeps unselected tabs out of the tab order;
              // the arrow keys are how they are reached.
              tabIndex={selected ? 0 : -1}
              onClick={() => selectTab(tab)}
              // `outline-offset-[-2px]` because the strip clips its overflow: a
              // ring drawn outside the tab would be cut off, leaving keyboard
              // focus invisible on the one control that needs it.
              className={`h-11 shrink-0 rounded-t-lg px-3 text-sm font-semibold transition-colors focus-visible:outline-offset-[-2px] ${
                selected
                  ? "border-b-2 border-primary text-on-background"
                  : "border-b-2 border-transparent text-muted hover:text-on-background"
              }`}
            >
              {TAB_LABELS[tab]}
            </button>
          );
        })}
      </div>

      {user ? (
        <div
          id={panelId(activeTab)}
          role="tabpanel"
          aria-labelledby={tabId(activeTab)}
          // Focusable so a keyboard user can reach the panel and scroll it; the
          // global `:focus-visible` ring is left in place, which is what shows
          // where focus landed.
          tabIndex={0}
        >
          <SettingsPanel tab={activeTab} />
        </div>
      ) : (
        // Unreachable behind `ProtectedRoute`, but the cached user is the only
        // source for every field on this page. An empty form would read as real
        // (and wrong) account data, so say plainly that it is not known yet.
        <Card className="p-6">
          <SkeletonText lines={5} />
        </Card>
      )}
    </div>
  );
}

