import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { PRIORITY_ORDER } from "../api/types";
import type { Category, ReviewStatus, TicketView } from "../api/types";
import type { useToasts } from "./useToasts";

export interface Filters {
  status: ReviewStatus | "";
  category: Category | "";
  escalatedOnly: boolean;
}

export type SortKey = "priority" | "received" | "id";

export interface BulkState {
  running: boolean;
  done: number;
  total: number;
  failed: number;
}

export const EMPTY_FILTERS: Filters = { status: "", category: "", escalatedOnly: false };
const IDLE_BULK: BulkState = { running: false, done: 0, total: 0, failed: 0 };

const label = (value: string) => value.replace(/_/g, " ");

/**
 * All queue state in one place.
 *
 * The components below this are presentational; this hook owns fetching,
 * optimistic updates, rollback, and — new — the feedback that goes with each of
 * those. Every code path that talks to the server ends in either a toast or a
 * visible state change, because the previous version could succeed and fail
 * with the same amount of on-screen evidence: none.
 */
export function useReviewQueue(toast: ReturnType<typeof useToasts>["toast"]) {
  const [items, setItems] = useState<TicketView[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("priority");
  const [triaging, setTriaging] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<BulkState>(IDLE_BULK);

  // Lets the bulk run be abandoned when the component unmounts mid-flight.
  const cancelled = useRef(false);
  useEffect(() => () => { cancelled.current = true; }, []);

  const load = useCallback(
    async (opts: { quiet?: boolean } = {}) => {
      if (!opts.quiet) setLoading(true);
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
            : null,
        );
      } catch (err) {
        toast.error(
          "Could not load the queue",
          err instanceof ApiError ? err.message : "Something went wrong.",
        );
      } finally {
        setLoading(false);
      }
    },
    [filters, toast],
  );

  useEffect(() => {
    void load();
    // `load` is recreated when filters change, which is exactly when we want to
    // refetch. Search and sort are client-side and deliberately do not refetch.
    // This effect firing more than once per filter change means a dependency
    // upstream lost its identity — `toast` is memoised in useToasts for exactly
    // that reason.
  }, [load]);

  const patchItem = useCallback((id: string, patch: Partial<TicketView>) => {
    setItems((current) =>
      current.map((item) => (item.ticket.id === id ? { ...item, ...patch } : item)),
    );
  }, []);

  const markBusy = useCallback((id: string, busy: boolean) => {
    setTriaging((s) => {
      const next = new Set(s);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  /**
   * Triage one ticket on demand.
   *
   * Per-ticket rather than on list load: the list endpoint serves triage from
   * cache only, so a cold queue renders instantly and fills in as you go
   * (PRD 16). Thirty synchronous model calls on first paint would be unusable.
   *
   * `silent` is for the bulk run, which reports once at the end rather than
   * stacking thirty toasts.
   */
  const runTriage = useCallback(
    async (id: string, force = false, silent = false) => {
      markBusy(id, true);
      try {
        const triage = await api.runTriage(id, force);
        patchItem(id, { triage });

        if (!silent) {
          const detail = `${label(triage.category)} · ${triage.priority}${
            triage.escalate ? " · escalated for review" : ""
          }`;
          if (triage.degraded) {
            toast.warn(
              `${id} classified from the fallback`,
              `${detail}. The model was unreachable, so this came from keyword matching — treat it as a guess.`,
            );
          } else {
            toast.success(force ? `${id} re-classified` : `${id} classified`, detail);
          }
        }
        return triage;
      } catch (err) {
        if (!silent) {
          toast.error(
            `Could not classify ${id}`,
            err instanceof ApiError ? err.message : "Triage failed.",
          );
        }
        return null;
      } finally {
        markBusy(id, false);
      }
    },
    [markBusy, patchItem, toast],
  );

  /**
   * Classify everything that has no cached result yet.
   *
   * Sequential, not parallel: Ollama serves one 3B model on the reviewer's own
   * machine, and firing thirty concurrent requests at it makes every one of them
   * slower and some of them time out. One at a time with a progress bar is both
   * faster in wall-clock terms and honest about what is happening.
   */
  const triageAll = useCallback(async () => {
    const pending = items.filter((i) => !i.triage).map((i) => i.ticket.id);
    if (pending.length === 0) {
      toast.info("Nothing to classify", "Every ticket in the queue already has a result.");
      return;
    }

    cancelled.current = false;
    setBulk({ running: true, done: 0, total: pending.length, failed: 0 });

    let done = 0;
    let failed = 0;
    for (const id of pending) {
      if (cancelled.current) break;
      const result = await runTriage(id, false, true);
      if (result) done++;
      else failed++;
      setBulk({ running: true, done, total: pending.length, failed });
    }

    setBulk(IDLE_BULK);
    if (failed === 0) {
      toast.success(
        `Classified ${done} ticket${done === 1 ? "" : "s"}`,
        "The queue is up to date. Escalated items are worth opening first.",
      );
    } else {
      toast.warn(
        `Classified ${done} of ${pending.length}`,
        `${failed} failed. Check that the backend and Ollama are both running, then try again.`,
      );
    }
  }, [items, runTriage, toast]);

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

      try {
        const saved = await api.updateReview(id, {
          status,
          edited_reply: editedReply,
          note,
          version: before.review.version,
        });
        patchItem(id, { review: saved });
        const changed = editedReply !== null && editedReply !== (before.triage?.suggested_reply ?? "");
        toast.success(
          `${id} ${status}`,
          changed
            ? "Your edited reply was saved with the decision. Nothing was sent to the customer."
            : "Decision recorded. Nothing was sent to the customer.",
        );
      } catch (err) {
        patchItem(id, { review: before.review }); // roll back
        if (err instanceof ApiError && err.isConflict) {
          toast.warn(
            "Someone else changed this ticket",
            "Your change was not saved because another tab edited it first. Reloading the queue so you can redo it on the current version.",
          );
          void load({ quiet: true });
        } else {
          toast.error(
            `Could not save ${id}`,
            err instanceof ApiError ? err.message : "The decision was not recorded.",
          );
        }
      }
    },
    [items, patchItem, load, toast],
  );

  /**
   * Client-side search and sort.
   *
   * Both stay off the wire on purpose: the whole corpus is 30 tickets, already
   * in memory, and a round trip per keystroke would make search feel worse than
   * no search. Category / status / escalated filters *are* server-side, because
   * those are the ones the API already models.
   */
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matched = needle
      ? items.filter((i) => {
          const t = i.ticket;
          return (
            t.id.toLowerCase().includes(needle) ||
            t.subject.toLowerCase().includes(needle) ||
            t.body.toLowerCase().includes(needle) ||
            t.sender.toLowerCase().includes(needle) ||
            (i.triage?.summary.toLowerCase().includes(needle) ?? false)
          );
        })
      : items;

    const ranked = [...matched];
    if (sort === "priority") {
      // Untriaged sink to the bottom: an unknown priority is not a low one, but
      // it is also not something a reviewer can act on until it has been run.
      ranked.sort((a, b) => {
        const ai = a.triage ? PRIORITY_ORDER.indexOf(a.triage.priority) : 99;
        const bi = b.triage ? PRIORITY_ORDER.indexOf(b.triage.priority) : 99;
        if (ai !== bi) return ai - bi;
        return a.ticket.id.localeCompare(b.ticket.id);
      });
    } else if (sort === "received") {
      ranked.sort((a, b) => b.ticket.received_at.localeCompare(a.ticket.received_at));
    } else {
      ranked.sort((a, b) => a.ticket.id.localeCompare(b.ticket.id));
    }
    return ranked;
  }, [items, search, sort]);

  // Keep the selection valid when a search or filter hides the current ticket.
  useEffect(() => {
    if (loading || visible.length === 0) return;
    if (!selectedId || !visible.some((i) => i.ticket.id === selectedId)) {
      setSelectedId(visible[0]!.ticket.id);
    }
  }, [visible, selectedId, loading]);

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

  const filtersActive =
    filters.status !== "" || filters.category !== "" || filters.escalatedOnly || search.trim() !== "";

  const clearFilters = useCallback(() => {
    setFilters(EMPTY_FILTERS);
    setSearch("");
  }, []);

  return {
    items: visible,
    allItems: items,
    selected,
    selectedId,
    setSelectedId,
    filters,
    setFilters,
    filtersActive,
    clearFilters,
    search,
    setSearch,
    sort,
    setSort,
    loading,
    counts,
    triaging,
    bulk,
    runTriage,
    triageAll,
    submitReview,
    reload: () => load(),
  };
}
