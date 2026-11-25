import Link from "next/link";

import { api } from "@/lib/api";
import { modelName, money } from "@/lib/format";

import { OUTCOME_LABEL, OutcomeBadge, outcome, type Outcome } from "../_components/RecommendationCard";
import { Empty, PageHeader } from "../_components/ui";

export default async function Recommendations() {
  const recs = await api.recommendations();
  const priced = recs.filter((r) => r.est_monthly_cost_current != null);
  const current = priced.reduce((sum, r) => sum + (r.est_monthly_cost_current ?? 0), 0);
  const recommended = priced.reduce((sum, r) => sum + (r.est_monthly_cost_recommended ?? 0), 0);
  const savings = current - recommended;
  const counts = recs.reduce<Partial<Record<Outcome, number>>>((acc, r) => {
    const o = outcome(r.rationale);
    acc[o] = (acc[o] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <PageHeader
        title="Model fit"
        description="For each AI system, the model that fits its task best, weighing capability, cost, where the data goes and speed. Prices are illustrative."
      />

      {recs.length === 0 ? (
        <Empty title="No recommendations yet">Run discovery to compare the models in use.</Empty>
      ) : (
        <>
          <div className="mb-8 flex items-end justify-between gap-8 rounded-xl border border-line bg-card p-6">
            <div>
              <p className="text-sm text-ink-2">{savings >= 0 ? "Monthly savings if every recommendation is taken" : "Extra monthly cost if every recommendation is taken"}</p>
              <p className="mt-3 text-[40px] leading-none font-medium text-accent tabular-nums">{money(Math.abs(savings))}</p>
              <p className="mt-3 text-sm text-ink-3 tabular-nums">{money(current)} a month now, {money(recommended)} recommended</p>
            </div>
            <dl className="flex gap-8 text-sm">
              {(Object.keys(OUTCOME_LABEL) as Outcome[]).filter((o) => counts[o]).map((o) => (
                <div key={o} className="text-right">
                  <dt className="text-ink-3">{OUTCOME_LABEL[o]}</dt>
                  <dd className="mt-1 text-xl font-medium tabular-nums">{counts[o]}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="overflow-hidden rounded-xl border border-line bg-card">
            <div className="grid grid-cols-[1fr_120px_120px_120px] gap-4 border-b border-line bg-page/60 px-5 py-2 text-xs text-ink-3">
              <span>System and recommendation</span>
              <span className="text-right">Now a month</span>
              <span className="text-right">Recommended</span>
              <span className="text-right">Savings</span>
            </div>
            <ul>
              {recs.map((r) => {
                const diff = r.est_monthly_cost_current != null && r.est_monthly_cost_recommended != null
                  ? r.est_monthly_cost_current - r.est_monthly_cost_recommended : null;
                return (
                  <li key={r.registry_id} className="grid grid-cols-[1fr_120px_120px_120px] gap-4 border-t border-line px-5 py-4 first:border-t-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-3">
                        <Link href={`/inventory/${r.registry_id}`} className="text-sm font-medium hover:text-accent">{r.name}</Link>
                        <OutcomeBadge rationale={r.rationale} />
                      </div>
                      <p className="mt-1 text-sm">
                        <span className="text-ink-2">{modelName(r.current_model)}</span>
                        {r.recommended_model !== r.current_model && (
                          <>
                            <span className="mx-2 text-ink-3">to</span>
                            <span className="font-medium">{modelName(r.recommended_model)}</span>
                          </>
                        )}
                      </p>
                      <p className="mt-2 max-w-3xl text-sm leading-5 text-ink-2">{r.rationale}</p>
                    </div>
                    <span className="text-right text-sm tabular-nums">{money(r.est_monthly_cost_current)}</span>
                    <span className="text-right text-sm tabular-nums">{money(r.est_monthly_cost_recommended)}</span>
                    <span className={`text-right text-sm tabular-nums ${diff && diff > 0 ? "font-medium text-accent" : "text-ink-2"}`}>
                      {diff == null ? "-" : diff > 0 ? money(diff) : diff < 0 ? `${money(-diff)} more` : "$0.00"}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}
    </>
  );
}
