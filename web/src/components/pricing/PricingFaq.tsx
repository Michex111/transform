import { useState } from "react";
import { Link } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { CaretDown } from "@phosphor-icons/react";
import { Card } from "@/components/ui";
import { PRICING_FAQS } from "@/lib/pricingFaq";

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
          {PRICING_FAQS.map((faq, index) => {
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
