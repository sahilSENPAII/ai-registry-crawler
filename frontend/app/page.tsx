import Link from "next/link";

import { api, type Risk } from "@/lib/api";
import { CATEGORY, RULE, label, money } from "@/lib/format";

import { Card, Empty, PageHeader, RiskBadge, SeverityBadge } from "./_components/ui";

const RISKS: { level: Risk; label: string; bar: string }[] = [
  { level: "high", label: "High risk", bar: "bg-high-bar" },
  { level: "medium", label: "Medium risk", bar: "bg-medium-bar" },
  { level: "low", label: "Low risk", bar: "bg-low-bar" },
];

function Stat({ label, value, note, className = "" }: { label: string; value: React.ReactNode; note: string; className?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-card p-5 ${className}`}>
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-3 text-[32px] leading-none font-medium tabular-nums">{value}</p>
      <p className="mt-3 text-xs text-ink-3">{note}</p>
    </div>
  );
}

export default async function Overview() {
  const [summary, findings, agents, highRisk] = await Promise.all([
    api.summary(),
    api.findings(),
    api.agents(),
    api.systems({ risk_level: "high" }),
  ]);

  if (summary.systems === 0) {
    return (
      <>
        <PageHeader title="Overview" />
        <Empty title="No AI systems in the registry yet">Run discovery to scan the AWS account.</Empty>
      </>
    );
  }

  const sev = summary.findings_by_severity;
  const urgent = (sev.critical ?? 0) + (sev.high ?? 0);
  const shadow = agents.filter((a) => !a.registered).length;
  const { current, recommended } = summary.monthly_cost;
  const savings = current - recommended;
  const high = summary.by_risk_level.high ?? 0;
  const categories = Object.entries(summary.by_category).sort((a, b) => b[1] - a[1]);
  const maxCategory = Math.max(...categories.map(([, n]) => n));
  const scanned = summary.last_run?.resources_scanned;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Every AI system found in the AWS account, how risky each one is, and what needs attention first"
      />

      <div className="grid grid-cols-4 gap-4">
        <Stat label="AI systems" value={summary.systems} note={scanned ? `found among ${scanned} cloud resources` : "in the registry"} />
        <Stat label="Agents" value={summary.agents} note={`${summary.monitored} systems send activity logs`} />
        <Stat label="High-risk systems" value={high} note={`${Math.round((high / summary.systems) * 100)}% of AI systems`} />
        <Stat
          label="Open critical and high issues"
          value={urgent}
          note={`${sev.critical ?? 0} critical, ${sev.high ?? 0} high, ${findings.length} issues in total`}
        />
        <Stat label="Shadow agents" value={shadow} note="active in the logs but missing from the inventory" />
        <div className="col-span-3 flex items-center justify-between gap-8 rounded-xl border border-line bg-card p-5">
          <div>
            <p className="text-sm text-ink-2">Monthly model cost</p>
            <p className="mt-3 text-[32px] leading-none font-medium tabular-nums">
              {money(current)}
              <span className="mx-3 text-ink-3">to</span>
              {money(recommended)}
            </p>
            <p className="mt-3 text-xs text-ink-3">now, and with the recommended models</p>
          </div>
          <div className="text-right">
            <p className="text-sm text-ink-2">{savings >= 0 ? "Savings" : "Extra cost"}</p>
            <p className="mt-3 text-[32px] leading-none font-medium text-accent tabular-nums">{money(Math.abs(savings))}</p>
            <p className="mt-3 text-xs text-ink-3">
              a month, {current ? Math.round((Math.abs(savings) / current) * 100) : 0}% of today&apos;s spend
            </p>
          </div>
        </div>
      </div>

      <div className="mt-8 grid grid-cols-3 gap-4">
        <Card title="Risk across the AI estate" aside={`${summary.systems} systems`} className="col-span-2">
          <div className="flex h-10 overflow-hidden rounded-lg" role="img" aria-label="Systems by risk level">
            {RISKS.map(({ level, bar }) => {
              const n = summary.by_risk_level[level] ?? 0;
              return n ? <div key={level} className={`${bar} border-r-2 border-card last:border-r-0`} style={{ width: `${(n / summary.systems) * 100}%` }} /> : null;
            })}
          </div>
          <div className="mt-3 grid grid-cols-3 text-sm">
            {RISKS.map(({ level, label: name, bar }) => (
              <Link key={level} href={`/inventory?risk=${level}`} className="group flex items-baseline gap-2">
                <span className={`size-2 rounded-full ${bar}`} aria-hidden />
                <span className="text-ink-2 group-hover:text-ink">{name}</span>
                <span className="font-medium tabular-nums">{summary.by_risk_level[level] ?? 0}</span>
              </Link>
            ))}
          </div>

          <h3 className="mt-8 text-sm text-ink-3">High-risk systems</h3>
          <ul className="mt-2">
            {highRisk.map((s) => (
              <li key={s.id} className="border-t border-line first:border-t-0">
                <Link href={`/inventory/${s.id}`} className="flex h-11 items-center gap-4 text-sm hover:text-accent">
                  <span className="w-56 truncate font-medium">{s.name}</span>
                  <span className="w-32 text-ink-2">{label(CATEGORY, s.category)}</span>
                  <span className="flex-1 truncate text-ink-3">{s.owner ?? "No owner"}</span>
                  <RiskBadge level={s.risk_level} score={s.risk_score} />
                </Link>
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Systems by kind" className="self-start">
          <ul className="flex flex-col gap-4">
            {categories.map(([category, n]) => (
              <li key={category}>
                <Link href={`/inventory?category=${category}`} className="group block">
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="text-ink-2 group-hover:text-ink">{label(CATEGORY, category)}</span>
                    <span className="font-medium tabular-nums">{n}</span>
                  </div>
                  <div className="mt-2 h-1.5 rounded-full bg-raised">
                    <div className="h-full rounded-full bg-ink-3" style={{ width: `${(n / maxCategory) * 100}%` }} />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Needs attention" aside={<Link href="/findings" className="hover:text-ink">All issues</Link>} className="mt-8">
        {findings.length === 0 ? (
          <p className="text-sm text-ink-2">No issues in the agent activity logs</p>
        ) : (
          <ul>
            {findings.slice(0, 5).map((f) => (
              <li key={f.id} className="flex items-center gap-4 border-t border-line py-3 first:border-t-0">
                <span className="w-20"><SeverityBadge severity={f.severity} /></span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{label(RULE, f.rule)}</p>
                  <p className="truncate text-sm text-ink-2">{f.description}</p>
                </div>
                <Link href={`/agents/${encodeURIComponent(f.agent_name)}`} className="w-48 truncate text-sm text-ink-2 hover:text-ink">
                  {f.agent_name}
                </Link>
                <Link
                  href={`/agents/${encodeURIComponent(f.agent_name)}${f.trace_id ? `?trace=${f.trace_id}` : ""}`}
                  className="w-20 text-right text-sm font-medium text-accent hover:underline"
                >
                  {f.trace_id ? "View run" : "View agent"}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
