export function money(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n > 0 && n < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

export function tokens(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "-";
  if (ms < 1_000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export function relative(iso: string, now = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3_600)}h ago`;
  if (seconds < 30 * 86_400) return `${Math.floor(seconds / 86_400)}d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function exact(iso: string): string {
  return `${new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC`;
}

export const CATEGORY: Record<string, string> = {
  ai_agent: "Agent",
  genai_application: "GenAI app",
  ai_workflow: "AI workflow",
  predictive_ml: "Predictive model",
};

export const AUTONOMY: Record<string, string> = {
  autonomous: "Acts on its own",
  supervised: "Human approves actions",
  assistive: "Assists people",
  automated_scoring: "Automated scoring",
};

export const OVERSIGHT: Record<string, string> = { approval_gate: "Approval step", none: "None" };

export const SERVICE: Record<string, string> = {
  lambda: "Lambda",
  "bedrock-agent": "Bedrock agent",
  sagemaker: "SageMaker",
  stepfunctions: "Step Functions",
  ecs: "ECS",
};

export const RULE: Record<string, string> = {
  unregistered_agent: "Agent not in the inventory",
  undeclared_tool: "Used a tool it never declared",
  side_effect_without_approval: "Took an action without approval",
  tool_loop: "Called the same tool over and over",
  token_spike: "Unusually large run",
  run_error: "Run failed",
  approval_denied: "Approver said no",
};

export const OPERATION: Record<string, string> = {
  invoke_agent: "Run",
  chat: "Model call",
  execute_tool: "Tool",
  retrieval: "Retrieval",
  human_approval: "Approval",
};

export const SIGNAL: Record<string, string> = {
  model_call: "Calls a model from its code",
  ai_sdk: "Imports an AI SDK",
  bedrock_agent: "Is a Bedrock agent",
  ml_image: "Serves an ML framework image",
  workflow_ai_step: "A workflow step calls a model",
  agent_framework_env: "Configured with an agent framework",
  managed_agent: "Managed agent service",
  tool_definitions: "Declares tools it can call",
  model_loop: "Keeps calling the model until the task is done",
  agent_framework: "Built on an agent framework",
  memory: "Remembers earlier sessions",
  iteration_budget: "Has a limit on steps per run",
  tool_choice_auto: "Lets the model pick tools",
  iam_permission_only: "Only holds an AI permission",
};

// shortcut: display names for the catalog's model ids; extend when the catalog grows
const MODEL_NAMES: Record<string, string> = {
  "anthropic.claude-3-opus-20240229-v1:0": "Claude 3 Opus",
  "anthropic.claude-3-5-sonnet-20240620-v1:0": "Claude 3.5 Sonnet",
  "anthropic.claude-3-sonnet-20240229-v1:0": "Claude 3 Sonnet",
  "anthropic.claude-3-haiku-20240307-v1:0": "Claude 3 Haiku",
  "meta.llama3-1-70b-instruct-v1:0": "Llama 3.1 70B",
  "meta.llama3-1-8b-instruct-v1:0": "Llama 3.1 8B",
  "amazon.nova-pro-v1:0": "Nova Pro",
  "amazon.nova-lite-v1:0": "Nova Lite",
  "amazon.nova-micro-v1:0": "Nova Micro",
  "gpt-4o": "GPT-4o",
  "gpt-4o-mini": "GPT-4o mini",
};

export function modelName(id: string | null | undefined): string {
  return id ? (MODEL_NAMES[id] ?? id) : "-";
}

export function label(map: Record<string, string>, key: string | null | undefined): string {
  return key ? (map[key] ?? key) : "-";
}
