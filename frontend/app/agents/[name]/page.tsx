import Link from "next/link";

import { ApiError, api, type Span, type Tool } from "@/lib/api";
import { duration, money, tokens } from "@/lib/format";

import { Waterfall } from "../../_components/Waterfall";
import { Card, Empty, Table, Tag, Td, Th, Time } from "../../_components/ui";

async function traceOrNull(traceId: string): Promise<Span[] | null> {
  try {
    return await api.trace(traceId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export default async function AgentPage({ params, searchParams }: {
  params: Promise<{ name: string }>;
  searchParams: Promise<{ trace?: string }>;
}) {
  const name = decodeURIComponent((await params).name);
  const { trace } = await searchParams;
  const [runs, agents] = await Promise.all([api.agentRuns(name), api.agents()]);
  const agent = agents.find((a) => a.name === name);
  const declared: Tool[] | null = agent?.registry_id ? (await api.system(agent.registry_id)).tools : null;

  const selected = trace ?? runs[0]?.trace_id;
  const spans = selected ? await traceOrNull(selected) : null;
  const selectedRun = runs.find((r) => r.trace_id === selected);

  return (
    <>
      <Link href="/agents" className="text-sm text-ink-3 hover:text-ink">Agent activity</Link>
      <header className="mt-2 mb-8 flex items-center gap-3">
        <h1 className="text-[28px] leading-tight font-medium">{name}</h1>
        {agent && !agent.registered && <Tag tone="warn">Shadow agent, not registered</Tag>}
        {agent?.registry_id && (
          <Link href={`/inventory/${agent.registry_id}`} className="ml-auto text-sm font-medium text-accent hover:underline">
            Inventory record
          </Link>
        )}
      </header>

      {runs.length === 0 ? (
        <Empty title="No activity recorded for this agent">Its runs appear here once its logs are read by a discovery run.</Empty>
      ) : (
        <div className="flex flex-col gap-8">
          <Card
            title={selectedRun ? "Run steps" : "Run steps for a linked run"}
            aside={selectedRun && <>Started <Time iso={selectedRun.started_at} /></>}
          >
            {spans ? (
              <Waterfall spans={spans} declared={declared} />
            ) : (
              <p className="text-sm text-ink-2">This run is not in the activity logs any more</p>
            )}
          </Card>

          <section>
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-base font-medium">Runs</h2>
              <span className="text-sm text-ink-3 tabular-nums">{runs.length} in the log window</span>
            </div>
            <Table
              head={
                <>
                  <Th>Started</Th>
                  <Th right>Duration</Th>
                  <Th right>Steps</Th>
                  <Th right>Tokens</Th>
                  <Th right>Cost</Th>
                  <Th right>Result</Th>
                </>
              }
            >
              {runs.map((r) => {
                const active = r.trace_id === selected;
                return (
                  <tr key={r.trace_id} className={`relative transition-colors duration-150 ${active ? "bg-accent-soft" : "hover:bg-page"}`}>
                    <Td>
                      <Link
                        href={`/agents/${encodeURIComponent(name)}?trace=${r.trace_id}`}
                        aria-current={active ? "true" : undefined}
                        className="after:absolute after:inset-0"
                      >
                        <Time iso={r.started_at} />
                      </Link>
                    </Td>
                    <Td right>{duration(r.duration_ms)}</Td>
                    <Td right>{r.spans}</Td>
                    <Td right>{tokens(r.tokens)}</Td>
                    <Td right>{money(r.cost_usd)}</Td>
                    <Td right className={r.status === "error" ? "font-medium text-high" : "text-ink-2"}>
                      {r.status === "error" ? "Failed" : "Completed"}
                    </Td>
                  </tr>
                );
              })}
            </Table>
          </section>
        </div>
      )}
    </>
  );
}
