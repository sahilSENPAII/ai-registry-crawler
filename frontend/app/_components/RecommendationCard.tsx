import type { Outcome, Recommendation } from "@/lib/api";
import { modelName, money } from "@/lib/format";

import { Card, Tag } from "./ui";

export const OUTCOME_LABEL: Record<Outcome, string> = {
  keep: "Keep",
  downgrade: "Downgrade",
  upgrade: "Upgrade",
  residency: "Residency switch",
  switch: "Switch",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <Tag tone={outcome === "keep" ? "neutral" : "accent"}>{OUTCOME_LABEL[outcome]}</Tag>;
}

export function RecommendationCard({ rec }: { rec: Recommendation }) {
  const changes = rec.current_model !== rec.recommended_model;
  return (
    <Card title="Model fit" aside={<OutcomeBadge outcome={rec.outcome} />}>
      <p className="text-sm">
        <span className="text-ink-2">{modelName(rec.current_model)}</span>
        {changes && (
          <>
            <span className="mx-2 text-ink-3">to</span>
            <span className="font-medium">{modelName(rec.recommended_model)}</span>
          </>
        )}
      </p>
      {rec.est_monthly_cost_current != null && (
        <p className="mt-2 text-sm tabular-nums text-ink-2">
          {money(rec.est_monthly_cost_current)}
          {changes && <> to {money(rec.est_monthly_cost_recommended)}</>} a month
        </p>
      )}
      <p className="mt-3 text-sm leading-5 text-ink-2">{rec.rationale}</p>
    </Card>
  );
}
