"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { PUBLIC_API } from "@/lib/api";

type State =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; systems: number; findings: number }
  | { kind: "busy" }
  | { kind: "failed"; message: string };

export function RunDiscovery() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "idle" });

  async function run() {
    setState({ kind: "running" });
    try {
      const res = await fetch(`${PUBLIC_API}/discovery/run`, { method: "POST" });
      if (res.status === 409) {
        setState({ kind: "busy" });
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setState({ kind: "failed", message: body?.detail ?? `The API returned ${res.status}` });
        return;
      }
      const summary = await res.json();
      setState({ kind: "done", systems: summary.ai_systems_found, findings: summary.findings });
      router.refresh();
    } catch {
      setState({ kind: "failed", message: "The registry API is not reachable" });
    }
  }

  const message = {
    idle: null,
    running: "Scanning the account",
    done: state.kind === "done" ? `Done: ${state.systems} AI systems, ${state.findings} issues` : null,
    busy: "A run is already in progress",
    failed: state.kind === "failed" ? state.message : null,
  }[state.kind];

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={run}
        disabled={state.kind === "running"}
        className="h-9 rounded-lg bg-accent px-4 text-sm font-medium text-white transition-[background-color,transform] duration-150 ease-out hover:bg-accent-hover active:scale-[0.98] disabled:cursor-wait disabled:opacity-70"
      >
        {state.kind === "running" ? "Running discovery" : "Run discovery"}
      </button>
      <p aria-live="polite" className={`min-h-4 text-xs ${state.kind === "failed" || state.kind === "busy" ? "text-high" : "text-ink-3"}`}>
        {message}
      </p>
    </div>
  );
}
