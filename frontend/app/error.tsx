"use client";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto mt-16 max-w-lg rounded-xl border border-line bg-card p-8">
      <h1 className="text-lg font-medium">The registry is not available right now</h1>
      <p className="mt-2 text-sm text-ink-2">
        The dashboard could not load data from the registry API. Check that the API is running, then try again.
      </p>
      {error.message && <p className="mt-4 font-mono text-xs text-ink-3">{error.message}</p>}
      <button
        type="button"
        onClick={reset}
        className="mt-6 h-9 rounded-lg border border-line bg-raised px-4 text-sm font-medium transition-colors duration-150 hover:bg-card"
      >
        Try again
      </button>
    </div>
  );
}
