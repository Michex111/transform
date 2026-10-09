import { describe, expect, it } from "vitest";

import { PRICING_FAQS } from "@/lib/pricingFaq";
import { faqJsonLd } from "@/lib/seo";

describe("pricing FAQ data", () => {
  it("has entries", () => {
    expect(PRICING_FAQS.length).toBeGreaterThan(0);
  });

  it("gives every entry a non-empty question and answer", () => {
    // An empty answer would still be schema-valid, so nothing else would catch
    // it — the page would simply ship a rich result with a blank answer.
    for (const item of PRICING_FAQS) {
      expect(item.q.trim().length).toBeGreaterThan(0);
      expect(item.a.trim().length).toBeGreaterThan(0);
    }
  });

  it("asks each question once", () => {
    const questions = PRICING_FAQS.map((item) => item.q);
    expect(new Set(questions).size).toBe(questions.length);
  });

  it("converts to one FAQ entity per entry", () => {
    const ld = faqJsonLd(PRICING_FAQS.map((faq) => ({ question: faq.q, answer: faq.a })));
    expect((ld.mainEntity as unknown[]).length).toBe(PRICING_FAQS.length);
  });
});
