/**
 * Billing history on the Billing page.
 *
 * Invoices are the primary record, so they get the table; the credit ledger is
 * secondary and lives inside a disclosure so it cannot compete with them. A
 * failed invoice request, an unconfigured Stripe account and a brand-new
 * account all render one honest explanatory line — never an empty table, which
 * reads as "you have never been billed".
 */

import { CaretDown, DownloadSimple, Receipt } from "@phosphor-icons/react";
import { Badge, Card, Skeleton, SkeletonText } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { invoiceRows, invoiceSectionState, type InvoiceStatusTone } from "@/lib/invoices";
import type { CreditTransactionResponse, InvoiceListResponse } from "@/api/types";

const TONE_COLOR: Record<InvoiceStatusTone, string> = {
  success: "var(--color-success)",
  warning: "var(--color-warning)",
  error: "var(--color-error)",
  muted: "var(--color-muted)",
};

export interface BillingHistoryCardProps {
  loading: boolean;
  invoices: InvoiceListResponse | null;
  invoicesFailure: string | null;
  history: CreditTransactionResponse[];
}

export function BillingHistoryCard({
  loading,
  invoices,
  invoicesFailure,
  history,
}: BillingHistoryCardProps) {
  const state = invoiceSectionState(invoices, invoicesFailure);
  const rows = state.kind === "table" && invoices ? invoiceRows(invoices.invoices) : [];

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-outline px-5 py-4">
        <Receipt size={18} className="shrink-0 text-muted" aria-hidden />
        <h2 className="font-display text-lg font-semibold">Billing history</h2>
      </div>

      {/* Invoices */}
      <div className="px-5 py-4">
        <h3 className="text-sm font-medium">Invoices</h3>

        {loading ? (
          <div className="mt-3 space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center justify-between gap-4">
                <SkeletonText lines={2} />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        ) : state.kind === "message" ? (
          <p className="mt-2 text-sm text-muted">{state.message}</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <caption className="sr-only">Your invoices</caption>
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted">
                  <th scope="col" className="py-2 pr-3 font-semibold">
                    Invoice
                  </th>
                  <th scope="col" className="py-2 pr-3 font-semibold">
                    Date
                  </th>
                  <th scope="col" className="py-2 pr-3 font-semibold">
                    Amount
                  </th>
                  <th scope="col" className="py-2 pr-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="py-2 font-semibold">
                    Download
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline">
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="max-w-[12rem] truncate py-3 pr-3 font-mono text-xs text-on-background">
                      {row.number}
                    </td>
                    <td className="whitespace-nowrap py-3 pr-3 text-muted">{row.dateLabel}</td>
                    <td className="whitespace-nowrap py-3 pr-3 font-mono text-on-background">
                      {row.amountLabel}
                    </td>
                    <td className="py-3 pr-3">
                      <Badge color={TONE_COLOR[row.statusTone]}>{row.statusLabel}</Badge>
                    </td>
                    <td className="py-3">
                      {row.downloadUrl ? (
                        <a
                          href={row.downloadUrl}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={`Download invoice ${row.number}`}
                          className="inline-flex items-center gap-1 text-sm font-semibold text-primary underline-offset-4 hover:underline"
                        >
                          <DownloadSimple size={14} weight="bold" aria-hidden />
                          PDF
                        </a>
                      ) : (
                        <span className="text-muted" aria-label="No download available">
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Credit activity — deliberately a disclosure so it never competes with
          the invoices above. */}
      <details className="border-t border-outline">
        <summary className="group flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 text-sm font-medium text-on-background hover:bg-surface-variant/40">
          <span>Credit activity</span>
          <CaretDown
            size={16}
            className="shrink-0 text-muted transition-transform group-open:rotate-180"
            aria-hidden
          />
        </summary>
        <div className="px-5 pb-4">
          {loading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-center justify-between gap-4">
                  <SkeletonText lines={2} />
                  <Skeleton className="h-4 w-16" />
                </div>
              ))}
            </div>
          ) : history.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted">No credit activity yet.</p>
          ) : (
            <ul className="divide-y divide-outline">
              {history.map((entry) => (
                <li
                  key={entry.id}
                  className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-0.5 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-on-background capitalize">
                      {entry.transaction_type}
                    </p>
                    {entry.reference_id && (
                      <p className="truncate font-mono text-xs text-muted">{entry.reference_id}</p>
                    )}
                  </div>
                  <div className="flex flex-col items-end">
                    <span className="font-mono text-sm text-on-background">
                      {entry.amount > 0 ? `+${entry.amount}` : entry.amount}
                    </span>
                    <span className="font-mono text-xs text-muted">
                      {formatDate(entry.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>
    </Card>
  );
}
