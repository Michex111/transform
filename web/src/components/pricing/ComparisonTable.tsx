import { Check, X } from "@phosphor-icons/react";
import type { SubscriptionPlanResponse } from "@/api/types";
import { comparisonSections } from "@/lib/planComparison";

/** One cell: text, a check/cross, or a dash for "the API did not state it". */
function CellValue({ value }: { value: string | boolean | null }) {
  if (value === null) {
    return (
      <span className="text-muted">
        <span aria-hidden>—</span>
        <span className="sr-only">Not specified</span>
      </span>
    );
  }

  if (typeof value === "boolean") {
    return value ? (
      <Check
        size={16}
        weight="bold"
        className="text-success"
        role="img"
        aria-label="Included"
      />
    ) : (
      <X size={16} weight="bold" className="text-muted" role="img" aria-label="Not included" />
    );
  }

  return <span className="text-on-background">{value}</span>;
}

/**
 * A real `<table>` comparing every plan. The wrapper scrolls horizontally so
 * the page itself never overflows on a phone; the table keeps a readable
 * minimum width and a sticky header row.
 */
export function ComparisonTable({ plans }: { plans: SubscriptionPlanResponse[] }) {
  const sections = comparisonSections(plans);
  if (sections.length === 0) return null;

  const columns = plans.length + 1;
  const headCell =
    "sticky top-0 z-10 border-b border-outline bg-surface px-4 py-3 text-left text-sm font-semibold";

  return (
    <section id="compare" aria-labelledby="compare-heading" className="mt-16 scroll-mt-24">
      <h2 id="compare-heading" className="font-display text-2xl font-semibold">
        Compare plans
      </h2>

      <div className="mt-6 overflow-x-auto rounded-xl border border-outline">
        <table className="w-full min-w-[40rem] border-collapse text-sm">
          <caption className="sr-only">
            Feature comparison of every subscription plan
          </caption>
          <thead>
            <tr>
              <th scope="col" className={headCell}>
                Feature
              </th>
              {plans.map((plan) => (
                <th key={plan.tier} scope="col" className={headCell}>
                  {plan.name}
                </th>
              ))}
            </tr>
          </thead>

          {sections.map((section) => (
            <tbody key={section.title}>
              <tr>
                <th
                  scope="colgroup"
                  colSpan={columns}
                  className="border-b border-outline bg-surface-variant/40 px-4 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted"
                >
                  {section.title}
                </th>
              </tr>
              {section.rows.map((row) => (
                <tr key={row.label} className="border-b border-outline last:border-b-0">
                  <th
                    scope="row"
                    className="px-4 py-2.5 text-left font-normal text-muted"
                  >
                    {row.label}
                  </th>
                  {row.values.map((value, index) => (
                    <td key={plans[index].tier} className="px-4 py-2.5">
                      <CellValue value={value} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </section>
  );
}
