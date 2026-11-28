"use client";

import { useState } from "react";

import type { Span, Tool } from "@/lib/api";
import { OPERATION, duration, label, modelName, money } from "@/lib/format";

const BAR: Record<string, string> = {
  invoke_agent: "bg-op-run",
  chat: "bg-op-model",
  execute_tool: "bg-op-tool",
  retrieval: "bg-op-retrieval",
  human_approval: "bg-op-approval",
};
const LEGEND = ["chat", "execute_tool", "retrieval", "human_approval"];

function title(s: Span): string {
  const a = s.attributes;
  if (s.operation === "chat") return modelName(s.model);
  if (s.operation === "execute_tool") return [s.tool_name ?? "tool", a["crawler.resource"]].filter(Boolean).join(" ");
  if (s.operation === "retrieval") return String(a["gen_ai.data_source.id"] ?? "knowledge base");
  if (s.operation === "human_approval") return `${a["approval.action"] ?? "action"}: ${a["approval.decision"] ?? "pending"}`;
  return s.agent_name;
}

function amount(s: Span): number | null {
  try {
    const args = JSON.parse(String(s.attributes["gen_ai.tool.call.arguments"] ?? "{}"));
    return typeof args.amount === "number" ? args.amount : null;
  } catch {
    return null;
  }
}

export function Waterfall({ spans, declared }: { spans: Span[]; declared: Tool[] | null }) {
  const [selected, setSelected] = useState<number | null>(null);
  const start = Math.min(...spans.map((s) => Date.parse(s.started_at)));
  const end = Math.max(...spans.map((s) => Date.parse(s.ended_at ?? s.started_at)));
  const total = Math.max(end - start, 1);
  const sideEffects = new Set(declared?.filter((t) => t.side_effect).map((t) => t.name));
  const declaredNames = declared ? new Set(declared.map((t) => t.name)) : null;
  const picked = spans.find((s) => s.id === selected);

  return (
    <div>
      <div className="mb-4 flex items-center gap-5 text-xs text-ink-2">
        {LEGEND.map((op) => (
          <span key={op} className="flex items-center gap-2">
            <span className={`h-2 w-4 rounded-sm ${BAR[op]}`} aria-hidden />
            {label(OPERATION, op)}
          </span>
        ))}
        <span className="ml-auto text-ink-3 tabular-nums">{duration(total)} in total</span>
      </div>

      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 right-56 left-72 grid grid-cols-4" aria-hidden>
          {[0, 1, 2, 3].map((i) => <div key={i} className="border-l border-line" />)}
        </div>
        <ul className="relative flex flex-col gap-1">
          {spans.map((s) => {
            const offset = ((Date.parse(s.started_at) - start) / total) * 100;
            const width = Math.max(((s.duration_ms ?? 0) / total) * 100, 0.6);
            const tool = s.operation === "execute_tool" ? s.tool_name : null;
            const sideEffect = tool != null && sideEffects.has(tool);
            const undeclared = tool != null && declaredNames != null && !declaredNames.has(tool);
            const value = amount(s);
            const guardrail = Boolean(s.attributes["crawler.guardrail"]);
            const rejected = Boolean(s.attributes["crawler.verdict.rejected"]);
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setSelected(s.id === selected ? null : s.id)}
                  aria-pressed={s.id === selected}
                  className={`flex h-8 w-full items-center rounded-md text-left text-sm transition-colors duration-150 ${
                    s.id === selected ? "bg-raised" : "hover:bg-page"
                  }`}
                >
                  <span className={`flex w-72 shrink-0 items-center gap-2 truncate pr-3 ${s.parent_span_id ? "pl-6" : "pl-2 font-medium"}`}>
                    <span className="w-20 shrink-0 text-xs text-ink-3">{label(OPERATION, s.operation)}</span>
                    <span className="truncate">{title(s)}</span>
                  </span>
                  <span className="relative h-full flex-1">
                    <span
                      className={`absolute top-2.5 h-3 rounded-sm ${BAR[s.operation] ?? "bg-op-run"} ${
                        s.status === "error" ? "ring-2 ring-high ring-offset-1" : ""
                      }`}
                      style={{ left: `${offset}%`, width: `${width}%` }}
                    />
                  </span>
                  <span className="flex w-56 shrink-0 items-center justify-end gap-2 pr-2 text-xs">
                    {value != null && <span className="text-ink-2 tabular-nums">{money(value)}</span>}
                    {sideEffect && <span className="font-medium text-ink-2">Changes data</span>}
                    {undeclared && <span className="font-medium text-high">Not declared</span>}
                    {guardrail && <span className="font-medium text-ink-2">Run by guardrail</span>}
                    {rejected && <span className="font-medium text-high">Verdict rejected</span>}
                    {s.operation === "chat" && s.cost_usd != null && <span className="text-ink-2 tabular-nums">{money(s.cost_usd)}</span>}
                    {s.status === "error" && <span className="font-medium text-high">Failed</span>}
                    <span className="w-14 text-right text-ink-3 tabular-nums">{duration(s.duration_ms)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {picked ? (
        <div className="mt-4 rounded-lg border border-line bg-page p-4">
          <div className="mb-3 flex items-baseline justify-between text-sm">
            <span className="font-medium">{picked.span_name}</span>
            <span className="text-ink-3 tabular-nums">
              {duration(picked.duration_ms)}
              {picked.cost_usd != null && ` · ${money(picked.cost_usd)}`}
            </span>
          </div>
          <dl className="grid grid-cols-[240px_1fr] gap-x-4 gap-y-1 font-mono text-xs">
            {Object.entries(picked.attributes).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-ink-3">{k}</dt>
                <dd className="break-all text-ink">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : (
        <p className="mt-4 text-xs text-ink-3">Select a step to see what was recorded for it</p>
      )}
    </div>
  );
}
