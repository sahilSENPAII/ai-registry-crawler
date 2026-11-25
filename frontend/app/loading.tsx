export default function Loading() {
  return (
    <div role="status" className="flex flex-col gap-6">
      <div className="h-8 w-56 rounded-lg bg-raised" />
      <div className="grid grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 rounded-xl border border-line bg-card" />
        ))}
      </div>
      <div className="h-72 rounded-xl border border-line bg-card" />
      <span className="text-sm text-ink-3">Loading the registry</span>
    </div>
  );
}
