import Link from "next/link";

import { api } from "@/lib/api";
import { AUTONOMY, CATEGORY, OVERSIGHT, SERVICE, label } from "@/lib/format";

import { Empty, Filters, PageHeader, RiskBadge, Table, Td, Th } from "../_components/ui";

const CATEGORIES = [{ label: "All" }, ...Object.entries(CATEGORY).map(([value, l]) => ({ value, label: l }))];
const RISKS = [{ label: "All" }, { value: "high", label: "High" }, { value: "medium", label: "Medium" }, { value: "low", label: "Low" }];

export default async function Inventory({ searchParams }: { searchParams: Promise<{ category?: string; risk?: string }> }) {
  const { category, risk } = await searchParams;
  const systems = await api.systems({ category, risk_level: risk });

  const href = (next: { category?: string; risk?: string }) => {
    const q = new URLSearchParams(Object.entries({ category, risk, ...next }).filter((e): e is [string, string] => Boolean(e[1])));
    return q.size ? `/inventory?${q}` : "/inventory";
  };

  return (
    <>
      <PageHeader
        title="AI inventory"
        description="Every AI system in the account, with its risk, how much it acts on its own and who owns it"
      />
      <div className="mb-4 flex flex-wrap items-center gap-6">
        <Filters label="Kind" options={CATEGORIES} current={category} href={(v) => href({ category: v })} />
        <Filters label="Risk" options={RISKS} current={risk} href={(v) => href({ risk: v })} />
        <span className="ml-auto text-sm text-ink-3 tabular-nums">{systems.length} systems</span>
      </div>

      {systems.length === 0 ? (
        <Empty title="No AI systems match these filters">
          {category || risk ? "Clear a filter to see more." : "Run discovery to scan the AWS account."}
        </Empty>
      ) : (
        <Table
          head={
            <>
              <Th>System and service</Th>
              <Th>Kind</Th>
              <Th>Risk</Th>
              <Th>Autonomy</Th>
              <Th>Human oversight</Th>
              <Th>Owner</Th>
              <Th>Monitored</Th>
              <Th>Status</Th>
            </>
          }
        >
          {systems.map((s) => (
            <tr key={s.id} className="relative transition-colors duration-150 hover:bg-page">
              <Td className="h-14 whitespace-nowrap">
                <Link href={`/inventory/${s.id}`} className="font-medium after:absolute after:inset-0 hover:text-accent">
                  {s.name}
                </Link>
                <p className="text-xs text-ink-3">{label(SERVICE, s.service)}</p>
              </Td>
              <Td className="text-ink-2">{label(CATEGORY, s.category)}</Td>
              <Td><RiskBadge level={s.risk_level} score={s.risk_score} /></Td>
              <Td className="text-ink-2">{label(AUTONOMY, s.autonomy_level)}</Td>
              <Td className="text-ink-2">{label(OVERSIGHT, s.human_oversight)}</Td>
              <Td className="text-ink-2">{s.owner ?? <span className="text-ink-3">No owner</span>}</Td>
              <Td className="text-ink-2">{s.monitored ? "Yes" : <span className="text-ink-3">No logs</span>}</Td>
              <Td className={s.status === "active" ? "text-ink-2" : "text-ink-3"}>{s.status === "active" ? "Active" : "Retired"}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
