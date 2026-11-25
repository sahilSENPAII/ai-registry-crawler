"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Health = { status: string; db: string };

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="min-h-screen p-8 font-sans">
      <h1 className="text-3xl font-bold">AI Registry Crawler</h1>
      <p className="mt-4" role="status">
        {error
          ? `API unreachable at ${API_URL}: ${error}`
          : health
            ? `API: ${health.status} · DB: ${health.db}`
            : "Checking API…"}
      </p>
    </main>
  );
}
