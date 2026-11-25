"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/inventory", label: "AI inventory" },
  { href: "/agents", label: "Agent activity" },
  { href: "/findings", label: "Issues" },
  { href: "/recommendations", label: "Model fit" },
];

export function NavLinks() {
  const path = usePathname();
  return (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ href, label }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`flex h-9 items-center rounded-lg px-3 text-sm transition-colors duration-150 ${
              active ? "bg-raised font-medium text-ink shadow-[inset_0_0_0_1px_var(--color-line)]" : "text-ink-2 hover:bg-raised/60 hover:text-ink"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
