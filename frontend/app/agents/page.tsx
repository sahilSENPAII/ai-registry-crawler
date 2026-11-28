import Link from "next/link";

import { api } from "@/lib/api";
import { money, tokens } from "@/lib/format";

import { Empty, PageHeader, Table, Tag, Td, Th, Time } from "../_components/ui";

export default async function Agents() {
  const agents = await api.agents();
  const shadow = agents.filter((a) => !a.registered && !a.internal).length;
  const registered = agents.filter((a) => a.registered).length;

  return (
    <>
      <PageHeader
        title="Agent activity"
        description="What each agent did in the activity logs: how often it ran, what it cost and where it went wrong"
      >
        <p className="text-sm text-ink-3 tabular-nums">
          {registered} registered, {shadow} not in the inventory
        </p>
      </PageHeader>

      {agents.length === 0 ? (
        <Empty title="No agent activity yet">Agents appear here once their logs are read by a discovery run.</Empty>
      ) : (
        <Table
          head={
            <>
              <Th>Agent</Th>
              <Th>Inventory</Th>
              <Th right>Runs</Th>
              <Th right>Tokens</Th>
              <Th right>Cost</Th>
              <Th right>Failed runs</Th>
              <Th right>Issues</Th>
              <Th right>Last active</Th>
            </>
          }
        >
          {agents.map((a) => (
            <tr key={a.name} className="relative transition-colors duration-150 hover:bg-page">
              <Td className="font-medium">
                <Link href={`/agents/${encodeURIComponent(a.name)}`} className="after:absolute after:inset-0 hover:text-accent">
                  {a.name}
                </Link>
              </Td>
              <Td>
                {a.internal ? (
                  <Tag tone="accent">Our crawler</Tag>
                ) : a.registered ? (
                  <span className="text-ink-2">Registered</span>
                ) : (
                  <Tag tone="warn">Shadow agent, not registered</Tag>
                )}
              </Td>
              <Td right>{a.runs}</Td>
              <Td right>{tokens(a.tokens)}</Td>
              <Td right>{money(a.cost_usd)}</Td>
              <Td right className={a.error_runs ? "text-high" : "text-ink-3"}>{a.error_runs}</Td>
              <Td right className={a.findings ? "font-medium" : "text-ink-3"}>{a.findings}</Td>
              <Td right className="text-ink-2"><Time iso={a.last_seen} /></Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
