import type {
  Problem,
  Readiness,
  Review,
  ReviewStatus,
  TicketList,
  TicketView,
  Triage,
} from "./types";

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
    // A 5xx with no problem+json body is almost always the dev proxy reporting
    // that nothing is listening upstream — the backend died or was restarted.
    // "Request failed (500)" is accurate and useless; say what to actually do.
    const fallback =
      response.status >= 500 && !problem
        ? "The backend did not respond. It may have stopped — check its terminal window, then reload."
        : `Request failed (${response.status})`;

    throw new ApiError(
      response.status,
      problem,
      problem?.detail || problem?.title || fallback,
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

  /**
   * Readiness, read directly rather than through `request`.
   *
   * `/readyz` answers 503 when the LLM is unreachable but still returns a full,
   * meaningful body. Routing it through `request` would turn that into a thrown
   * ApiError and throw away the one payload that explains the degradation — so
   * this reads the body on both 200 and 503, and only treats a network-level
   * failure as "offline".
   */
  async readiness(): Promise<Readiness | null> {
    try {
      const response = await fetch(`${BASE}/api/v1/readyz`, {
        headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
      });
      if (response.status !== 200 && response.status !== 503) return null;
      return (await response.json()) as Readiness;
    } catch {
      return null; // backend not running at all
    }
  },
};
