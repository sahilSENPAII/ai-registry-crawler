import Link from "next/link";

import { api, type Run } from "@/lib/api";

import { NavLinks } from "./NavLinks";
import { RunDiscovery } from "./RunDiscovery";
import { Time } from "./ui";

export async function Sidebar() {
  let lastRun: Run | null = null;
  let reachable = true;
  try {
    [lastRun = null] = await api.runs(1);
  } catch {
    reachable = false;
  }

  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r border-line bg-card px-4 py-6">
      <Link href="/" className="px-3 text-base font-semibold tracking-tight">
        AI Registry Crawler
      </Link>
      <p className="mt-1 px-3 text-xs text-ink-3">ISO/IEC 42001 AI inventory</p>

      <div className="mt-8">
        <NavLinks />
      </div>

      <div className="mt-auto flex flex-col gap-4 border-t border-line px-3 pt-4">
        <div className="text-xs leading-5">
          <p className="text-ink-3">Last discovery run</p>
          {!reachable ? (
            <p className="text-high">API not reachable</p>
          ) : lastRun ? (
            <p className="text-ink-2">
              <Time iso={lastRun.finished_at ?? lastRun.started_at} />
              {lastRun.status !== "succeeded" && <span className="text-high"> · {lastRun.status}</span>}
            </p>
          ) : (
            <p className="text-ink-2">Never</p>
          )}
        </div>
        <RunDiscovery />
      </div>
    </aside>
  );
}
