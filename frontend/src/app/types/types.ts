export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: "pending" | "completed" | "error" | "interrupted";
}

export interface SubAgent {
  id: string;
  name: string;
  subAgentName: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown>;
  status: "pending" | "active" | "completed" | "error";
  toolCalls?: ToolCall[];
}

export interface FileItem {
  path: string;
  content: string;
}

export interface TodoItem {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed";
  updatedAt?: Date;
}

export interface Thread {
  id: string;
  title: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface InterruptData {
  value: any;
  ns?: string[];
  scope?: string;
}

export interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

// Wire shape from langchain's HumanInTheLoopMiddleware (snake_case), paired
// index-for-index with `action_requests` — never key it by name.
export interface ReviewConfig {
  action_name: string;
  allowed_decisions?: string[];
  args_schema?: Record<string, unknown>;
}

export interface ToolApprovalInterruptData {
  action_requests: ActionRequest[];
  review_configs?: ReviewConfig[];
}

// One reviewer decision, mirroring the middleware's resume contract. The
// resume payload is `{ decisions: ApprovalDecision[] }` with exactly one
// decision per action_request, in order.
export type ApprovalDecision =
  | { type: "approve" }
  | { type: "edit"; edited_action: { name: string; args: Record<string, unknown> } }
  | { type: "reject"; message?: string };

// One pending action_request, resolved against its interrupt. `index`/`total`
// preserve the middleware's ordering contract; `namespace` targets subgraph
// (subagent) interrupts on resume.
export interface PendingApproval {
  key: string;
  interruptId: string;
  namespace?: string[];
  index: number;
  total: number;
  actionRequest: ActionRequest;
  reviewConfig?: ReviewConfig;
}

export interface Source {
  id: string;
  title: string;
  url: string;
  toolCallId: string;
}
