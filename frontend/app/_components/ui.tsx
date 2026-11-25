import Link from "next/link";

import type { Risk, Severity } from "@/lib/api";
import { exact, relative } from "@/lib/format";

const RISK_STYLE: Record<Risk, string> = {
  high: "bg-high-bg text-high",
  medium: "bg-medium-bg text-medium",
  low: "bg-low-bg text-low",
};
const RISK_DOT: Record<Risk, string> = { high: "bg-high-bar", medium: "bg-medium-bar", low: "bg-low-bar" };
const RISK_LABEL: Record<Risk, string> = { high: "High", medium: "Medium", low: "Low" };

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-high text-white",
  high: "bg-high-bg text-high",
  medium: "bg-medium-bg text-medium",
  low: "bg-raised text-ink-2",
};
const SEVERITY_LABEL: Record<Severity, string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

const pill = "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium whitespace-nowrap";

export function RiskBadge({ level, score }: { level: Risk; score?: number }) {
  return (
    <span className={`${pill} ${RISK_STYLE[level]}`}>
      <span className={`size-1.5 rounded-full ${RISK_DOT[level]}`} aria-hidden />
      {RISK_LABEL[level]}
      {score != null && <span className="tabular-nums opacity-80">{score}</span>}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`${pill} ${SEVERITY_STYLE[severity]}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function Tag({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "accent" | "warn" }) {
  const style = { neutral: "bg-raised text-ink-2", accent: "bg-accent-soft text-accent", warn: "bg-high-bg text-high" }[tone];
  return <span className={`${pill} ${style}`}>{children}</span>;
}

/** The signature detail: a register reference, set in mono like an auditor's citation. */
export function Ref({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-xs text-ink-3 tabular-nums">{children}</span>;
}

export function Time({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="text-ink-3">-</span>;
  return (
    <time dateTime={iso} title={exact(iso)} className="whitespace-nowrap">
      {relative(iso)}
    </time>
  );
}

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: React.ReactNode }) {
  return (
    <header className="mb-8 flex items-end justify-between gap-6">
      <div>
        <h1 className="text-[28px] leading-tight font-medium">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm text-ink-2">{description}</p>}
      </div>
      {children}
    </header>
  );
}

export function Card({ title, aside, children, className = "" }: {
  title?: string; aside?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-card p-6 ${className}`}>
      {title && (
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <h2 className="text-base font-medium">{title}</h2>
          {aside && <div className="text-sm text-ink-3">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      {children && <p className="mt-1 text-sm text-ink-2">{children}</p>}
    </div>
  );
}

export function Filters({ label, options, current, href }: {
  label: string; options: { value?: string; label: string }[]; current?: string; href: (value?: string) => string;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-sm text-ink-3">{label}</span>
      <div className="flex rounded-lg border border-line bg-card p-1">
        {options.map((o) => {
          const active = (o.value ?? "") === (current ?? "");
          return (
            <Link
              key={o.label}
              href={href(o.value)}
              aria-current={active ? "true" : undefined}
              className={`h-7 rounded-md px-3 text-sm leading-7 transition-colors duration-150 ${
                active ? "bg-raised font-medium text-ink shadow-[inset_0_0_0_1px_var(--color-line)]" : "text-ink-2 hover:text-ink"
              }`}
            >
              {o.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

export function Th({ children, right = false, className = "" }: { children?: React.ReactNode; right?: boolean; className?: string }) {
  return (
    <th className={`h-9 px-3 text-xs font-normal whitespace-nowrap text-ink-3 ${right ? "text-right" : "text-left"} ${className}`}>
      {children}
    </th>
  );
}

export function Td({ children, right = false, className = "" }: { children?: React.ReactNode; right?: boolean; className?: string }) {
  return <td className={`h-11 px-3 ${right ? "text-right tabular-nums" : ""} ${className}`}>{children}</td>;
}

export function Table({ head, children }: { head: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card">
      <table className="w-full text-sm">
        <thead className="border-b border-line bg-page/60">
          <tr>{head}</tr>
        </thead>
        <tbody className="[&>tr]:border-t [&>tr]:border-line [&>tr:first-child]:border-t-0">{children}</tbody>
      </table>
    </div>
  );
}
