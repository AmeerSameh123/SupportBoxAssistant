import type { Problem, Review, ReviewStatus, TicketList, TicketView, Triage } from "./types";

// Empty by default: Vite proxies /api to the backend, so the browser sees a
// same-origin request and no CORS negotiation happens at all.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

/** A backend error, carrying the parsed problem+json so the UI can show the real reason. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 409 means someone else changed this record; the UI must re-read, not retry. */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

  let response: Response;
  try {
    response = await fetch(`${BASE}/api/v1${path}`, { ...init, headers });
  } catch {
    // Network-level failure: the backend is not running. Distinguished from an
    // HTTP error so the UI can say something accurate.
    throw new ApiError(0, null, "Cannot reach the API. Is the backend running?");
  }

  if (!response.ok) {
    let problem: Problem | null = null;
    try {
      problem = (await response.json()) as Problem;
    } catch {
      problem = null;
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail || problem?.title || `Request failed (${response.status})`,
    );
  }

  return (await response.json()) as T;
}

export const api = {
  listTickets: (params: URLSearchParams) =>
    request<TicketList>(`/tickets?${params.toString()}`),

  getTicket: (id: string) => request<TicketView>(`/tickets/${id}`),

  runTriage: (id: string, force = false) =>
    request<Triage>(`/tickets/${id}/triage${force ? "?force=true" : ""}`, {
      method: "POST",
    }),

  updateReview: (
    id: string,
    body: {
      status: ReviewStatus;
      edited_reply: string | null;
      note: string | null;
      version: number;
    },
  ) => request<Review>(`/reviews/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
};
