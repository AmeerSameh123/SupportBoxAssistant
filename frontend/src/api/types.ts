// Mirrors backend/app/api/schemas.py.
//
// Hand-mirrored rather than generated from the OpenAPI spec. That is a conscious
// trade-off for a two-day build: generating types adds a codegen step and a
// dependency to justify, and there are eleven types here. The cost is that a
// backend change can drift from this file without the compiler noticing, which
// is called out in the README's limitations rather than left as a surprise.

export type Category =
  | "billing"
  | "bug"
  | "feature_request"
  | "account"
  | "security"
  | "other";

export type Priority = "low" | "medium" | "high" | "urgent";

export type ReviewStatus = "pending" | "approved" | "rejected";

export type TriageStage = "quality_gate" | "cache" | "llm" | "fallback";

export interface Telemetry {
  stage: TriageStage;
  attempts: number;
  latency_ms: number;
  repairs: string[];
  failures: string[];
  model: string;
  prompt_version: string;
  escalation_reasons: string[];
}

export interface Triage {
  category: Category;
  priority: Priority;
  summary: string;
  suggested_reply: string;
  confidence: number;
  escalate: boolean;
  degraded: boolean;
  injection_suspected: boolean;
  spam_suspected: boolean;
  telemetry: Telemetry;
}

export interface Ticket {
  id: string;
  received_at: string;
  channel: string;
  sender: string;
  subject: string;
  body: string;
}

export interface Review {
  ticket_id: string;
  status: ReviewStatus;
  edited_reply: string | null;
  note: string | null;
  version: number;
  updated_at: string | null;
}

export interface TicketView {
  ticket: Ticket;
  triage: Triage | null;
  review: Review;
}

export interface TicketList {
  items: TicketView[];
  total: number;
  limit: number;
  offset: number;
}

/** RFC 9457 problem+json, which is what every backend error looks like. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
}

export const PRIORITY_ORDER: Priority[] = ["urgent", "high", "medium", "low"];
