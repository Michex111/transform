/**
 * The public Pricing page's FAQ, as data.
 *
 * Lives outside the `PricingFaq` component so the same source can both render
 * the visible accordion and build the page's `FAQPage` structured data. Two
 * hand-maintained copies is exactly how a rich result ends up disagreeing with
 * the page it describes.
 *
 * Only facts true of this product. Anything the product does not actually do
 * (annual billing, a specific refund window) is left out rather than guessed.
 */
export interface PricingFaqItem {
  q: string;
  a: string;
}

export const PRICING_FAQS: PricingFaqItem[] = [
  {
    q: "Can I cancel anytime?",
    a: "Yes. Cancel from Billing and you keep your current plan until the end of the period you already paid for. You can resume before then if you change your mind.",
  },
  {
    q: "Can I switch plans mid-cycle?",
    a: "Yes. Upgrades take effect immediately and are prorated. Downgrades are scheduled for the end of your current billing period, so you keep the plan you already paid for.",
  },
  {
    q: "What happens to my credits when I upgrade?",
    a: "Any unspent plan credits carry over into a separate balance that expires at the end of the billing period you already paid for, so a mid-cycle upgrade does not lose them. Purchased credits are untouched.",
  },
  {
    q: "Do purchased credits expire?",
    a: "No. Purchased credit packs never expire, and plan credits refresh every month. In the app, plan credits are spent first, so routine conversions do not burn the balance you bought.",
  },
  {
    q: "How do payments and refunds work?",
    a: "Payments are processed by Stripe. If you need a refund or have a billing question, contact support and we will take a look.",
  },
  {
    q: "Which payment methods can I use?",
    a: "Card payments are handled securely by Stripe, and you can add or manage saved cards from Billing.",
  },
  {
    q: "How long do you keep my files?",
    a: "Guest conversions and their files are removed after 24 hours, and job history is archived after 30 days. Anything you delete is removed.",
  },
  {
    q: "Do you offer an Enterprise plan?",
    a: "Yes. Enterprise is custom-priced with the highest limits in the comparison above. Contact sales and we will put together a plan for your volume.",
  },
];
