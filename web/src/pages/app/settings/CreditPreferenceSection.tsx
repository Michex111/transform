/**
 * The "spend purchased credits first" preference.
 *
 * Rendered read-only, on purpose: no endpoint accepts this preference yet (the
 * profile PATCH takes names and the default save folder only), and this repo
 * does not ship confirm-then-error or fake-success UI. So the control shows the
 * account's real value from `/credits/balance` and a note that says plainly it
 * cannot be changed here, rather than a toggle that appears to save and does not.
 */

import { useEffect, useState } from "react";
import { Coins } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { Card, Skeleton } from "@/components/ui";
import { creditSpendingOrderCopy } from "@/lib/creditWallet";

export function CreditPreferenceSection() {
  const { api: client } = useAuth();
  // `undefined` means "still loading"; `null` means the API did not report it.
  const [preference, setPreference] = useState<boolean | null | undefined>(undefined);

  useEffect(() => {
    let active = true;
    client
      .creditBalance()
      .then((balance) => {
        if (active) setPreference(balance.purchased_credits_first ?? null);
      })
      .catch(() => {
        // The preference cannot be edited here either way, so a failed read is
        // not worth an error toast — it just reads as "unknown".
        if (active) setPreference(null);
      });
    return () => {
      active = false;
    };
  }, [client]);

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
            disabled
            className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition-colors ${
              copy.checked ? "border-primary bg-primary" : "border-outline-strong bg-surface-variant"
            } cursor-not-allowed opacity-60`}
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
