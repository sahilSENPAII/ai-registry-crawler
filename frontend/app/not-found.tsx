import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto mt-16 max-w-lg rounded-xl border border-line bg-card p-8">
      <h1 className="text-lg font-medium">Not in the registry</h1>
      <p className="mt-2 text-sm text-ink-2">
        This system, agent or run does not exist, or it was removed in a later discovery run.
      </p>
      <Link href="/inventory" className="mt-6 inline-block text-sm font-medium text-accent hover:underline">
        Back to the AI inventory
      </Link>
    </div>
  );
}
