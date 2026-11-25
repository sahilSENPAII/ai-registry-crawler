// Server components read from INTERNAL_API_URL (inside docker that is the api service);
// the browser uses NEXT_PUBLIC_API_URL.
const SERVER_API = process.env.INTERNAL_API_URL ?? "http://localhost:8000";
export const PUBLIC_API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Risk = "high" | "medium" | "low";
export type Severity = "critical" | "high" | "medium" | "low";

export type Run = {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "succeeded" | "failed";
  mode: string;
  regions: string[];
  resources_scanned: number;
  ai_systems_found: number;
  agents_found: number;
  findings_count: number;
  error: string | null;
};

export type Summary = {
  systems: number;
  agents: number;
  monitored: number;
  by_category: Record<string, number>;
  by_risk_level: Partial<Record<Risk, number>>;
  findings_by_severity: Partial<Record<Severity, number>>;
  monthly_cost: { current: number; recommended: number };
  last_run: Run | null;
};

export type SystemRow = {
  id: string;
  name: string;
  service: string;
  region: string;
  category: string;
  is_agent: boolean;
  risk_level: Risk;
  risk_score: number;
  autonomy_level: string;
  human_oversight: string;
  owner: string | null;
  environment: string;
  data_classification: string | null;
  monitored: boolean;
  status: "active" | "retired";
  last_seen_at: string;
};

export type Tool = { name: string; side_effect: boolean };
export type Evidence = { signal: string; weight: number; detail: string };
export type Control = { ref: string; title: string; why: string };

export type Finding = {
  id: number;
  run_id: number;
  registry_id: string | null;
  agent_name: string;
  rule: string;
  severity: Severity;
  trace_id: string | null;
  span_id: string | null;
  description: string;
  details: Record<string, unknown>;
  detected_at: string;
};

export type Alternative = { model: string; score: number; est_monthly_cost: number };
export type Outcome = "keep" | "downgrade" | "upgrade" | "residency" | "switch";

export type Recommendation = {
  registry_id: string;
  run_id: number;
  task_type: string;
  current_model: string | null;
  recommended_model: string | null;
  current_score: number | null;
  recommended_score: number | null;
  est_monthly_cost_current: number | null;
  est_monthly_cost_recommended: number | null;
  outcome: Outcome;
  rationale: string;
  alternatives: Alternative[];
  created_at: string;
};

export type RecommendationRow = Recommendation & { name: string; category: string; risk_level: Risk };

export type SystemDetail = SystemRow & {
  resource_arn: string;
  account_id: string;
  agent_score: number;
  evidence: Evidence[];
  models: string[];
  provider: string | null;
  frameworks: string[];
  tools: Tool[];
  data_sources: string[];
  task_type: string;
  purpose: string;
  customer_facing: boolean | null;
  decisions_about_people: boolean;
  risk_factors: string[];
  impact_assessment_status: string;
  controls: Control[];
  log_group: string | null;
  unused_ai_permissions: string[];
  first_seen_at: string;
  last_run_id: number;
  tags: Record<string, string>;
  recommendation: Recommendation | null;
  findings: Finding[];
  run_stats: { runs: number; spans: number; tokens: number; cost_usd: number; error_runs: number; last_seen: string | null };
};

export type Agent = {
  name: string;
  registry_id: string | null;
  registered: boolean;
  internal: boolean;
  runs: number;
  spans: number;
  tokens: number;
  cost_usd: number;
  error_runs: number;
  findings: number;
  last_seen: string;
};

export type AgentRun = {
  trace_id: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  spans: number;
  tokens: number;
  cost_usd: number;
  status: "ok" | "error";
};

export type Span = {
  id: number;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  agent_name: string;
  operation: string;
  span_name: string;
  model: string | null;
  tool_name: string | null;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  status: "ok" | "error";
  error_type: string | null;
  attributes: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${SERVER_API}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError(0, `The registry API at ${SERVER_API} is not reachable`);
  }
  if (!res.ok) throw new ApiError(res.status, `${path} returned ${res.status}`);
  return (await res.json()) as T;
}

function query(params: Record<string, string | undefined>): string {
  const entries = Object.entries(params).filter((e): e is [string, string] => Boolean(e[1]));
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
}

const id = encodeURIComponent;

export const api = {
  summary: () => get<Summary>("/summary"),
  runs: (limit = 20) => get<Run[]>(`/runs?limit=${limit}`),
  systems: (f: { category?: string; risk_level?: string } = {}) => get<SystemRow[]>(`/systems${query(f)}`),
  system: (registryId: string) => get<SystemDetail>(`/systems/${id(registryId)}`),
  agents: () => get<Agent[]>("/agents"),
  agentRuns: (name: string) => get<AgentRun[]>(`/agents/${id(name)}/runs`),
  trace: (traceId: string) => get<Span[]>(`/traces/${id(traceId)}`),
  findings: (f: { severity?: string; agent?: string } = {}) => get<Finding[]>(`/findings${query(f)}`),
  recommendations: () => get<RecommendationRow[]>("/recommendations"),
};
