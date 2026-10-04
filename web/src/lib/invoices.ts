/**
 * The invoice table on the Billing page.
 *
 * Amounts arrive from Stripe in the currency's minor unit (cents for USD), so
 * the single place that knows how to turn them into copy lives here. The
 * download link is guarded with the same `trustedExternalUrl` allowlist used for
 * every other API-supplied navigation, because an invoice PDF URL is just as
 * attacker-influenced as a checkout URL.
 *
 * Deliberately pure and free of React: this repo's test environment is Node (no
 * jsdom), so "which rows exist, what does each one say, and is it safe to
 * follow" is decided and tested here.
 */

import { trustedExternalUrl } from "@/lib/download";
import { formatDate } from "@/lib/format";
import type { InvoiceListResponse, InvoiceResponse } from "@/api/types";

/**
 * Currencies Stripe treats as having no minor unit. Everything else is cents,
 * except the three-decimal currencies below.
 */
const ZERO_DECIMAL = new Set([
  "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf",
  "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
]);
const THREE_DECIMAL = new Set(["bhd", "jod", "kwd", "omr", "tnd"]);

/**
 * Currency symbols we are confident enough to print without a locale. Anything
 * else falls back to the ISO code, which is always unambiguous.
 */
const SYMBOLS: Record<string, string> = {
  usd: "$",
  eur: "€",
  gbp: "£",
  jpy: "¥",
  cad: "CA$",
  aud: "A$",
  nzd: "NZ$",
  inr: "₹",
  brl: "R$",
};

function minorUnitExponent(currency: string): number {
  const code = currency.trim().toLowerCase();
  if (ZERO_DECIMAL.has(code)) return 0;
  if (THREE_DECIMAL.has(code)) return 3;
  return 2;
}

/**
 * Format a minor-unit amount: `formatMinorAmount(2499, "usd")` → `"$24.99"`,
 * `formatMinorAmount(100, "jpy")` → `"¥100"`, `formatMinorAmount(1234, "sek")`
 * → `"SEK 12.34"`.
 *
 * The decimal separator is always a dot and the digits are not grouped, which
 * keeps the output deterministic across the Node test runner and every browser
 * locale instead of relying on `Intl`'s locale-sensitive currency formatting.
 */
export function formatMinorAmount(amount: number, currency: string): string {
  const code = (currency || "usd").trim().toLowerCase();
  const exponent = minorUnitExponent(code);
  const value = Number.isFinite(amount) ? amount / 10 ** exponent : 0;
  const fixed = value.toFixed(exponent);
  const symbol = SYMBOLS[code];
  return symbol ? `${symbol}${fixed}` : `${code.toUpperCase()} ${fixed}`;
}

/** What the invoice actually cost: paid when settled, otherwise what is due. */
export function invoiceAmount(invoice: InvoiceResponse): string {
  const minor = invoice.amount_paid || invoice.amount_due;
  return formatMinorAmount(minor, invoice.currency);
}

/** The human-facing invoice number, falling back to the Stripe id. */
export function invoiceNumberLabel(invoice: InvoiceResponse): string {
  return invoice.number?.trim() || invoice.id;
}

/** The status as copy: `"past_due"` → `"Past due"`. */
export function invoiceStatusLabel(status: string): string {
  const value = (status ?? "").trim();
  if (!value) return "Unknown";
  const spaced = value.replace(/_/g, " ").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export type InvoiceStatusTone = "success" | "warning" | "error" | "muted";

export function invoiceStatusTone(status: string): InvoiceStatusTone {
  switch ((status ?? "").trim().toLowerCase()) {
    case "paid":
      return "success";
    case "open":
      return "warning";
    case "uncollectible":
      return "error";
    case "void":
    case "draft":
      return "muted";
    default:
      return "muted";
  }
}

/**
 * The URL to open for an invoice, or `null` when neither link is trusted.
 *
 * The PDF is preferred over the hosted page; both go through
 * `trustedExternalUrl`, so a `javascript:`/`data:`/plain-http value yields
 * `null` and the row renders a muted dash instead of a live link.
 */
export function invoiceDownloadUrl(invoice: InvoiceResponse): string | null {
  return (
    trustedExternalUrl(invoice.invoice_pdf ?? "") ??
    trustedExternalUrl(invoice.hosted_invoice_url ?? "")
  );
}

export interface InvoiceRow {
  id: string;
  number: string;
  dateLabel: string;
  amountLabel: string;
  statusLabel: string;
  statusTone: InvoiceStatusTone;
  downloadUrl: string | null;
}

/** The invoices as table rows, in the order the API returned them. */
export function invoiceRows(invoices: readonly InvoiceResponse[]): InvoiceRow[] {
  return invoices.map((invoice) => ({
    id: invoice.id,
    number: invoiceNumberLabel(invoice),
    dateLabel: formatDate(invoice.created_at),
    amountLabel: invoiceAmount(invoice),
    statusLabel: invoiceStatusLabel(invoice.status),
    statusTone: invoiceStatusTone(invoice.status),
    downloadUrl: invoiceDownloadUrl(invoice),
  }));
}

/** The section shows a table, or one honest line explaining why it cannot. */
export type InvoiceSectionState =
  | { kind: "table" }
  | { kind: "message"; message: string };

const NO_INVOICES_YET = "Invoices appear here after your first payment.";

/**
 * Whether to render the invoice table or a single explanatory line.
 *
 * `enabled: false` and a failed request are both ordinary states rather than an
 * error banner: Stripe may be unconfigured and a Free account has no customer
 * until checkout. An enabled-but-empty list also gets the line, so the section
 * never renders an empty table that reads as "you have never been billed".
 */
export function invoiceSectionState(
  response: InvoiceListResponse | null,
  failure: string | null,
): InvoiceSectionState {
  if (failure) return { kind: "message", message: failure };
  if (!response) return { kind: "message", message: NO_INVOICES_YET };
  if (!response.enabled) return { kind: "message", message: NO_INVOICES_YET };
  if (response.invoices.length === 0) return { kind: "message", message: NO_INVOICES_YET };
  return { kind: "table" };
}
