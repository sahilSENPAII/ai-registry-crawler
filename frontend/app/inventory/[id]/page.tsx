import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiError, api, type SystemDetail } from "@/lib/api";
import {
  AUTONOMY, CATEGORY, DECIDED_BY, OVERSIGHT, RULE, SERVICE, SIGNAL, label, modelName, money, tokens,
} from "@/lib/format";

import { RecommendationCard } from "../../_components/RecommendationCard";
import { Card, Ref, RiskBadge, SeverityBadge, Tag, Time } from "../../_components/ui";

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-3 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v ?? <span className="text-ink-3">-</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

const yesNo = (v: boolean | null) => (v == null ? <span className="text-ink-3">Not tagged</span> : v ? "Yes" : "No");
const list = (items: string[]) => (items.length ? items.join(", ") : <span className="text-ink-3">None found</span>);

async function load(id: string): Promise<SystemDetail> {
  try {
    return await api.system(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
}

export default async function SystemPage({ params }: { params: Promise<{ id: string }> }) {
  const s = await load((await params).id);

  return (
    <>
      <Link href="/inventory" className="text-sm text-ink-3 hover:text-ink">AI inventory</Link>
      <header className="mt-2 mb-8 flex items-start justify-between gap-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-[28px] leading-tight font-medium">{s.name}</h1>
            <RiskBadge level={s.risk_level} score={s.risk_score} />
            {s.status === "retired" && <Tag>Retired</Tag>}
          </div>
          <p className="mt-2 max-w-3xl text-sm text-ink-2">{s.purpose}</p>
        </div>
        <Ref>REG {s.id}</Ref>
      </header>

      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 flex flex-col gap-4">
          <Card title="Identity">
            <Facts rows={[
              ["Kind", label(CATEGORY, s.category)],
              ["AWS service", label(SERVICE, s.service)],
              ["Account and region", `${s.account_id} · ${s.region}`],
              ["Resource", <span key="arn" className="font-mono text-xs">{s.resource_arn}</span>],
              ["First seen", <Time key="f" iso={s.first_seen_at} />],
              ["Last seen", <Time key="l" iso={s.last_seen_at} />],
            ]} />
          </Card>

          <Card title="Accountability">
            <Facts rows={[
              ["Owner", s.owner ?? <span className="text-ink-3">No owner tag</span>],
              ["Environment", s.environment],
              ["Customer-facing", yesNo(s.customer_facing)],
              ["Decides about people", s.decisions_about_people ? "Yes" : "No"],
              ["Human oversight", label(OVERSIGHT, s.human_oversight)],
              ["Autonomy", label(AUTONOMY, s.autonomy_level)],
            ]} />
          </Card>

          <Card title="Data">
            <Facts rows={[
              ["Classification", s.data_classification ?? <span className="text-ink-3">Not tagged</span>],
              ["Data sources", list(s.data_sources)],
              ["Activity logs", s.log_group ? <span className="font-mono text-xs">{s.log_group}</span> : <span className="text-ink-3">None, so its actions are not visible</span>],
              ["Unused AI permissions", list(s.unused_ai_permissions)],
            ]} />
          </Card>

          <Card title="Models and tools">
            <Facts rows={[
              ["Models", s.models.length ? s.models.map(modelName).join(", ") : <span className="text-ink-3">None found</span>],
              ["Provider", s.provider],
              ["Frameworks", list(s.frameworks)],
              ["Task", s.task_type.replaceAll("_", " ")],
            ]} />
            {s.tools.length > 0 && (
              <ul className="mt-4 flex flex-wrap gap-2">
                {s.tools.map((t) => (
                  <li key={t.name}>
                    <Tag tone={t.side_effect ? "warn" : "neutral"}>
                      <span className="font-mono">{t.name}</span>
                      {t.side_effect && <span className="font-normal">changes data</span>}
                    </Tag>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Why we classified it this way" aside={s.is_agent ? `agent score ${s.agent_score} of 1.0` : undefined}>
            <Facts rows={[["Decided by", label(DECIDED_BY, s.decided_by)]]} />
            {s.llm_verdict?.reasoning && (
              <p className="mt-3 text-sm text-ink-2">
                <span className="text-ink-3">LLM reasoning: </span>
                {s.llm_verdict.reasoning}
              </p>
            )}
            {s.llm_verdict?.rejected && (
              <ul className="mt-3 flex flex-col gap-1 text-sm text-ink-2">
                {s.llm_verdict.rejected.map((reason) => (
                  <li key={reason} className="flex gap-2">
                    <span className="text-ink-3" aria-hidden>-</span>
                    Not accepted: {reason}
                  </li>
                ))}
              </ul>
            )}
            <ul className="mt-4 flex flex-col">
              {s.evidence.map((e, i) => (
                <li key={i} className="flex items-baseline gap-4 border-t border-line py-2 text-sm first:border-t-0">
                  <span className="w-72 shrink-0">{label(SIGNAL, e.signal)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-3">{e.detail}</span>
                  {e.weight > 0 && <span className="text-xs text-ink-3 tabular-nums">+{e.weight.toFixed(2)}</span>}
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="flex flex-col gap-4">
          <Card title="Risk" aside={`impact assessment ${s.impact_assessment_status.replace("_", " ")}`}>
            <p className="text-[32px] leading-none font-medium tabular-nums">
              {s.risk_score}
              <span className="ml-2 text-base text-ink-3">points</span>
            </p>
            <ul className="mt-4 flex flex-col gap-2 text-sm">
              {s.risk_factors.map((f) => (
                <li key={f} className="flex gap-2 text-ink-2">
                  <span className="text-ink-3" aria-hidden>-</span>
                  {f}
                </li>
              ))}
            </ul>
          </Card>

          <Card title="ISO/IEC 42001 controls" aside={`${s.controls.length} apply`}>
            <ul className="flex flex-col gap-3">
              {s.controls.map((c) => (
                <li key={c.ref} className="grid grid-cols-[56px_1fr] gap-x-3 text-sm">
                  <Ref>{c.ref}</Ref>
                  <div>
                    <p>{c.title}</p>
                    <p className="text-xs text-ink-3">Because {c.why}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Card>

          {s.recommendation && <RecommendationCard rec={s.recommendation} />}

          <Card title="Issues" aside={`${s.findings.length}`}>
            {s.findings.length === 0 ? (
              <p className="text-sm text-ink-2">
                {s.log_group ? "No issues in its activity logs" : "No activity logs, so behaviour is not checked"}
              </p>
            ) : (
              <ul className="flex flex-col gap-3">
                {s.findings.map((f) => (
                  <li key={f.id} className="text-sm">
                    <div className="flex items-center gap-2">
                      <SeverityBadge severity={f.severity} />
                      <span className="font-medium">{label(RULE, f.rule)}</span>
                    </div>
                    <p className="mt-1 text-ink-2">{f.description}</p>
                    {f.trace_id && (
                      <Link href={`/agents/${encodeURIComponent(f.agent_name)}?trace=${f.trace_id}`} className="text-sm font-medium text-accent hover:underline">
                        View run
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {s.run_stats.runs > 0 && (
              <p className="mt-4 border-t border-line pt-4 text-xs text-ink-3">
                {s.run_stats.runs} runs, {tokens(s.run_stats.tokens)} tokens, {money(s.run_stats.cost_usd)} in the log window
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
