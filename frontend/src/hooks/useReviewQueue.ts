import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Category, ReviewStatus, TicketView } from "../api/types";

export interface Filters {
  status: ReviewStatus | "";
  category: Category | "";
  escalatedOnly: boolean;
}

const EMPTY_FILTERS: Filters = { status: "", category: "", escalatedOnly: false };

/**
 * All queue state in one place.
 *
 * The components below this are presentational; this hook owns fetching,
 * optimistic updates and rollback. That split is what keeps the six components
 * small enough to read in one sitting.
 */
export function useReviewQueue() {
  const [items, setItems] = useState<TicketView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [triaging, setTriaging] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (filters.status) params.set("status", filters.status);
      if (filters.category) params.set("category", filters.category);
      if (filters.escalatedOnly) params.set("escalated", "true");
      const data = await api.listTickets(params);
      setItems(data.items);
      setSelectedId((current) =>
        current && data.items.some((i) => i.ticket.id === current)
          ? current
          : (data.items[0]?.ticket.id ?? null),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const patchItem = useCallback((id: string, patch: Partial<TicketView>) => {
    setItems((current) =>
      current.map((item) => (item.ticket.id === id ? { ...item, ...patch } : item)),
    );
  }, []);

  /**
   * Triage one ticket on demand.
   *
   * Per-ticket rather than on list load: the list endpoint serves triage from
   * cache only, so a cold queue renders instantly and fills in as you go
   * (PRD 16). Thirty synchronous model calls on first paint would be unusable.
   */
  const runTriage = useCallback(
    async (id: string, force = false) => {
      setTriaging((s) => new Set(s).add(id));
      setError(null);
      try {
        const triage = await api.runTriage(id, force);
        patchItem(id, { triage });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Triage failed.");
      } finally {
        setTriaging((s) => {
          const next = new Set(s);
          next.delete(id);
          return next;
        });
      }
    },
    [patchItem],
  );

  /**
   * Approve or reject, optimistically, with rollback.
   *
   * The rollback path is not decoration: a 409 means another tab already changed
   * this record, and silently keeping the optimistic value on screen would show
   * the reviewer a decision that was never saved.
   */
  const submitReview = useCallback(
    async (id: string, status: ReviewStatus, editedReply: string | null, note: string | null) => {
      const before = items.find((i) => i.ticket.id === id);
      if (!before) return;

      patchItem(id, {
        review: { ...before.review, status, edited_reply: editedReply, note },
      });
      setError(null);

      try {
        const saved = await api.updateReview(id, {
          status,
          edited_reply: editedReply,
          note,
          version: before.review.version,
        });
        patchItem(id, { review: saved });
      } catch (err) {
        patchItem(id, { review: before.review }); // roll back
        if (err instanceof ApiError && err.isConflict) {
          setError("This ticket was changed in another tab. Reloading the queue.");
          void load();
        } else {
          setError(err instanceof ApiError ? err.message : "Could not save.");
        }
      }
    },
    [items, patchItem, load],
  );

  const selected = useMemo(
    () => items.find((i) => i.ticket.id === selectedId) ?? null,
    [items, selectedId],
  );

  const counts = useMemo(
    () => ({
      total: items.length,
      pending: items.filter((i) => i.review.status === "pending").length,
      escalated: items.filter((i) => i.triage?.escalate).length,
      untriaged: items.filter((i) => !i.triage).length,
    }),
    [items],
  );

  return {
    items, selected, selectedId, setSelectedId,
    filters, setFilters, loading, error, counts,
    triaging, runTriage, submitReview, reload: load,
  };
}
