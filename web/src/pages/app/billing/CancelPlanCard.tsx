/**
 * The cancel card at the very bottom of the Billing page.
 *
 * Deliberately the quietest thing on the page and physically separated from
 * everything above: no hover lift, muted border, a single low-emphasis ghost
 * button. Changing the plan is the page's primary action; cancelling is a last
 * resort reached on purpose, and it opens the retention flow rather than
 * cancelling immediately.
 *
 * Rendered only for a paid subscription that is not already scheduled to end.
 */

import { Button, Card } from "@/components/ui";

export interface CancelPlanCardProps {
  onOpen: () => void;
}

export function CancelPlanCard({ onOpen }: CancelPlanCardProps) {
  return (
    <Card className="mt-10 border-outline p-6">
      <h2 className="font-display text-lg font-semibold">Cancel your plan</h2>
      <p className="mt-1 text-sm text-muted">
        Your plan keeps running until the end of the period you've already paid for. We'll ask a
        couple of questions before anything changes.
      </p>
      <div className="mt-4">
        <Button variant="ghost" size="sm" onClick={onOpen}>
          Cancel subscription
        </Button>
      </div>
    </Card>
  );
}
