/**
 * The "spend purchased credits first" preference.
 *
 * Savable via `PATCH /v1/credits/preference`. The value is read from
 * `/credits/balance` (the same source the Billing page uses, so the two cannot
 * disagree about the account's state) and saved with an optimistic update that
 * rolls back on failure — a switch that stayed flipped after a failed save
 * would be a lie about the account.
 *
 * The restriction the copy has to carry is that this only affects
 * **API-origin** conversions: browser conversions always spend plan credits
 * first. A user cannot infer that from the toggle alone.
 */

import { useEffect, useState } from "react";
import { Coins } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Card, Skeleton } from "@/components/ui";
import { creditSpendingOrderCopy } from "@/lib/creditWallet";

export function CreditPreferenceSection() {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  // `undefined` means "still loading"; `null` means the API did not report it.
  const [preference, setPreference] = useState<boolean | null | undefined>(undefined);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    client
      .creditBalance()
      .then((balance) => {
        if (active) setPreference(balance.purchased_credits_first ?? null);
      })
      .catch(() => {
        // A failed read is not worth an error toast on first paint — the note
        // already covers "we couldn't read this", and the control stays usable.
        if (active) setPreference(null);
      });
    return () => {
      active = false;
    };
  }, [client]);

  async function toggle(next: boolean) {
    if (saving) return;
    const previous = preference;
    // Optimistic: the switch moves immediately, then reverts if the save fails.
    setPreference(next);
    setSaving(true);
    try {
      const saved = await client.setCreditPreference(next);
      setPreference(saved.purchased_credits_first);
      success(
        saved.purchased_credits_first
          ? "API conversions will use purchased credits first."
          : "API conversions will use your plan credits first.",
      );
    } catch (err) {
      setPreference(previous ?? null);
      error(err instanceof Error ? err.message : "Could not save this setting");
    } finally {
      setSaving(false);
    }
  }

  const loading = preference === undefined;
  const copy = creditSpendingOrderCopy(preference ?? null);

  return (
    <Card className="p-6">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold">Credit spending order</h2>
        <Coins size={20} className="text-muted" aria-hidden="true" />
      </div>
      <p className="mb-4 text-sm text-muted">
        Choose whether API conversions spend purchased credits before your monthly plan credits.
      </p>

      <div className="flex items-start justify-between gap-4 rounded-lg border border-outline bg-surface-variant/40 px-3 py-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-on-background">Spend purchased credits first</p>
          <p className="mt-0.5 text-xs text-muted">
            Browser conversions always use your plan credits first, whatever this is set to.
          </p>
        </div>
        {loading ? (
          <Skeleton className="h-6 w-11 shrink-0" />
        ) : (
          <button
            type="button"
            role="switch"
            aria-checked={copy.checked}
            aria-label="Spend purchased credits first"
            aria-describedby="credit-spending-order-note"
            disabled={loading || saving}
            onClick={() => toggle(!copy.checked)}
            className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition-colors ${
              copy.checked ? "border-primary bg-primary" : "border-outline-strong bg-surface-variant"
            } ${loading || saving ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-on-background transition-transform ${
                copy.checked ? "left-[1.375rem]" : "left-0.5"
              }`}
            />
          </button>
        )}
      </div>

      <p id="credit-spending-order-note" className="mt-3 text-xs text-muted">
        {loading ? "Checking your current setting…" : copy.note}
      </p>
    </Card>
  );
}
