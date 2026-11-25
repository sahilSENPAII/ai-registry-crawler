import Link from "next/link";

import { api, type Severity } from "@/lib/api";
import { RULE, label } from "@/lib/format";

import { Empty, Filters, PageHeader, SeverityBadge, Time } from "../_components/ui";

const SEVERITIES: { value: Severity; label: string; note: string }[] = [
  { value: "critical", label: "Critical", note: "fix now" },
  { value: "high", label: "High", note: "fix this week" },
  { value: "medium", label: "Medium", note: "plan a fix" },
  { value: "low", label: "Low", note: "for information" },
];

export default async function Findings({ searchParams }: { searchParams: Promise<{ severity?: string; agent?: string }> }) {
  const { severity, agent } = await searchParams;
  const [findings, all] = await Promise.all([api.findings({ severity, agent }), api.findings()]);
  const agents = [...new Set(all.map((f) => f.agent_name))].sort();

  const href = (next: { severity?: string; agent?: string }) => {
    const q = new URLSearchParams(Object.entries({ severity, agent, ...next }).filter((e): e is [string, string] => Boolean(e[1])));
    return q.size ? `/findings?${q}` : "/findings";
  };

  return (
    <>
      <PageHeader
        title="Issues"
        description="Problems found in what the agents actually did, worst first. Each one links to the run that shows it."
      />
      <div className="mb-8 flex flex-wrap items-center gap-6">
        <Filters
          label="Severity"
          options={[{ label: "All" }, ...SEVERITIES.map(({ value, label: l }) => ({ value, label: l }))]}
          current={severity}
          href={(v) => href({ severity: v })}
        />
        <Filters label="Agent" options={[{ label: "All" }, ...agents.map((a) => ({ value: a, label: a }))]} current={agent} href={(v) => href({ agent: v })} />
      </div>

      {findings.length === 0 ? (
        <Empty title={all.length ? "No issues match these filters" : "No issues found"}>
          {all.length ? "Clear a filter to see more." : "Nothing unusual in the agent activity logs."}
        </Empty>
      ) : (
        <div className="flex flex-col gap-8">
          {SEVERITIES.map(({ value, label: name, note }) => {
            const group = findings.filter((f) => f.severity === value);
            if (!group.length) return null;
            return (
              <section key={value}>
                <h2 className="mb-3 flex items-baseline gap-3">
                  <span className="text-base font-medium">{name}</span>
                  <span className="text-sm text-ink-3 tabular-nums">{group.length} · {note}</span>
                </h2>
                <ul className="overflow-hidden rounded-xl border border-line bg-card">
                  {group.map((f) => (
                    <li key={f.id} className="flex items-start gap-4 border-t border-line px-5 py-4 first:border-t-0">
                      <SeverityBadge severity={f.severity} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{label(RULE, f.rule)}</p>
                        <p className="mt-1 text-sm text-ink-2">{f.description}</p>
                        <p className="mt-2 text-xs text-ink-3">
                          <Link href={`/agents/${encodeURIComponent(f.agent_name)}`} className="hover:text-ink">{f.agent_name}</Link>
                          {" · found "}
                          <Time iso={f.detected_at} />
                        </p>
                      </div>
                      <Link
                        href={`/agents/${encodeURIComponent(f.agent_name)}${f.trace_id ? `?trace=${f.trace_id}` : ""}`}
                        className="shrink-0 text-sm font-medium text-accent hover:underline"
                      >
                        {f.trace_id ? "View run" : "View agent"}
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
