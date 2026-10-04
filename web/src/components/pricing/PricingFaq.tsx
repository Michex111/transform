import { useState } from "react";
import { Link } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { CaretDown } from "@phosphor-icons/react";
import { Card } from "@/components/ui";

/**
 * Only facts true of this product. Anything the product does not actually do
 * (annual billing, a specific refund window) is left out rather than guessed.
 */
const FAQS = [
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

export function PricingFaq() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section aria-labelledby="faq-heading" className="mt-16">
      <Card className="overflow-hidden">
        <div className="border-b border-outline px-5 py-4">
          <h2 id="faq-heading" className="font-display text-lg font-semibold">
            Frequently asked
          </h2>
        </div>

        <ul className="divide-y divide-outline">
          {FAQS.map((faq, index) => {
            const expanded = open === index;
            const buttonId = `pricing-faq-button-${index}`;
            const panelId = `pricing-faq-panel-${index}`;
            return (
              <li key={faq.q}>
                <h3>
                  <button
                    id={buttonId}
                    type="button"
                    onClick={() => setOpen(expanded ? null : index)}
                    className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                    aria-expanded={expanded}
                    aria-controls={panelId}
                  >
                    <span className="text-sm font-medium text-on-background">{faq.q}</span>
                    <CaretDown
                      size={16}
                      className={`shrink-0 text-muted transition-transform ${
                        expanded ? "rotate-180" : ""
                      }`}
                      aria-hidden
                    />
                  </button>
                </h3>
                <AnimatePresence initial={false}>
                  {expanded && (
                    <motion.div
                      id={panelId}
                      role="region"
                      aria-labelledby={buttonId}
                      className="overflow-hidden"
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                    >
                      <p className="px-5 pb-4 text-sm text-muted">{faq.a}</p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </li>
            );
          })}
        </ul>

        <div className="border-t border-outline bg-surface-variant/30 px-5 py-4 text-sm text-muted">
          Still have questions?{" "}
          <Link to="/app/support" className="font-semibold text-primary hover:underline">
            Contact support
          </Link>
        </div>
      </Card>
    </section>
  );
}
