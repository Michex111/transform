/**
 * The invoice table's rules.
 *
 * The two that matter most: an amount is rendered from the currency's minor
 * unit correctly (a $24.99 invoice is 2499 cents, not $2499), and an untrusted
 * PDF URL never becomes a link.
 */

import { describe, expect, it } from "vitest";
import type { InvoiceResponse } from "@/api/types";
import {
  formatMinorAmount,
  invoiceAmount,
  invoiceDownloadUrl,
  invoiceNumberLabel,
  invoiceRows,
  invoiceSectionState,
  invoiceStatusLabel,
  invoiceStatusTone,
} from "./invoices";

function invoice(overrides: Partial<InvoiceResponse> = {}): InvoiceResponse {
  return {
    id: "in_123",
    number: "TRANSFORM-0001",
    status: "paid",
    amount_paid: 2499,
    amount_due: 2499,
    currency: "usd",
    created_at: "2026-10-01T12:00:00Z",
    period_start: null,
    period_end: null,
    invoice_pdf: "https://invoice.stripe.com/i/abc/pdf",
    hosted_invoice_url: "https://invoice.stripe.com/i/abc",
    ...overrides,
  };
}

describe("formatMinorAmount", () => {
  it("converts cents for a two-decimal currency", () => {
    expect(formatMinorAmount(2499, "usd")).toBe("$24.99");
    expect(formatMinorAmount(0, "usd")).toBe("$0.00");
    expect(formatMinorAmount(100000, "usd")).toBe("$1000.00");
  });

  it("treats a zero-decimal currency as whole units", () => {
    expect(formatMinorAmount(100, "jpy")).toBe("¥100");
  });

  it("uses the ISO code when it has no symbol", () => {
    expect(formatMinorAmount(1234, "sek")).toBe("SEK 12.34");
    expect(formatMinorAmount(1234, "")).toBe("$12.34");
  });

  it("does not print NaN for a malformed amount", () => {
    expect(formatMinorAmount(Number.NaN, "usd")).toBe("$0.00");
  });
});

describe("invoiceAmount / invoiceNumberLabel", () => {
  it("prefers what was paid and falls back to what is due", () => {
    expect(invoiceAmount(invoice())).toBe("$24.99");
    expect(invoiceAmount(invoice({ amount_paid: 0, amount_due: 1000 }))).toBe("$10.00");
  });

  it("falls back to the Stripe id when there is no number yet", () => {
    expect(invoiceNumberLabel(invoice())).toBe("TRANSFORM-0001");
    expect(invoiceNumberLabel(invoice({ number: null }))).toBe("in_123");
  });
});

describe("invoiceStatusLabel / invoiceStatusTone", () => {
  it("turns a status into copy and a tone", () => {
    expect(invoiceStatusLabel("past_due")).toBe("Past due");
    expect(invoiceStatusLabel("paid")).toBe("Paid");
    expect(invoiceStatusLabel("")).toBe("Unknown");
    expect(invoiceStatusTone("paid")).toBe("success");
    expect(invoiceStatusTone("open")).toBe("warning");
    expect(invoiceStatusTone("uncollectible")).toBe("error");
    expect(invoiceStatusTone("void")).toBe("muted");
    expect(invoiceStatusTone("new_stripe_value")).toBe("muted");
  });
});

describe("invoiceDownloadUrl", () => {
  it("prefers the PDF and falls back to the hosted page", () => {
    expect(invoiceDownloadUrl(invoice())).toBe("https://invoice.stripe.com/i/abc/pdf");
    expect(
      invoiceDownloadUrl(invoice({ invoice_pdf: null, hosted_invoice_url: "https://invoice.stripe.com/i/abc" })),
    ).toBe("https://invoice.stripe.com/i/abc");
  });

  it("refuses a URL that is not on the allowlist", () => {
    expect(invoiceDownloadUrl(invoice({ invoice_pdf: "javascript:alert(1)" }))).toBe(
      "https://invoice.stripe.com/i/abc",
    );
    expect(
      invoiceDownloadUrl(
        invoice({ invoice_pdf: "javascript:alert(1)", hosted_invoice_url: "http://evil.example/x" }),
      ),
    ).toBeNull();
    expect(invoiceDownloadUrl(invoice({ invoice_pdf: null, hosted_invoice_url: null }))).toBeNull();
  });
});

describe("invoiceRows", () => {
  it("builds one row per invoice", () => {
    const rows = invoiceRows([invoice(), invoice({ id: "in_456", number: null, status: "open" })]);
    expect(rows).toHaveLength(2);
    expect(rows[0].number).toBe("TRANSFORM-0001");
    expect(rows[0].statusTone).toBe("success");
    expect(rows[1].number).toBe("in_456");
    expect(rows[1].statusLabel).toBe("Open");
  });
});

describe("invoiceSectionState", () => {
  it("shows the table only when there is something to show", () => {
    expect(invoiceSectionState({ enabled: true, invoices: [invoice()] }, null)).toEqual({
      kind: "table",
    });
  });

  it("explains rather than rendering an empty table", () => {
    const message = { kind: "message", message: "Invoices appear here after your first payment." };
    expect(invoiceSectionState({ enabled: false, invoices: [] }, null)).toEqual(message);
    expect(invoiceSectionState({ enabled: true, invoices: [] }, null)).toEqual(message);
    expect(invoiceSectionState(null, null)).toEqual(message);
  });

  it("surfaces a failure message verbatim", () => {
    expect(invoiceSectionState(null, "Could not load invoices")).toEqual({
      kind: "message",
      message: "Could not load invoices",
    });
  });
});
