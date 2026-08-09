import type { Category, ReviewStatus, TicketView } from "../api/types";
import type { Filters } from "../hooks/useReviewQueue";
import { QueueItem } from "./QueueItem";

const CATEGORIES: Category[] = [
  "billing", "bug", "feature_request", "account", "security", "other",
];
const STATUSES: ReviewStatus[] = ["pending", "approved", "rejected"];

export function QueueList({
  items, selectedId, filters, loading, triaging, counts, onSelect, onFilters, onReload,
}: {
  items: TicketView[];
  selectedId: string | null;
  filters: Filters;
  loading: boolean;
  triaging: Set<string>;
  counts: { total: number; pending: number; escalated: number; untriaged: number };
  onSelect: (id: string) => void;
  onFilters: (f: Filters) => void;
  onReload: () => void;
}) {
  return (
    <aside className="queue">
      <header className="queue-header">
        <h1>Review queue</h1>
        <p className="muted small">
          {counts.total} tickets · {counts.pending} pending · {counts.escalated} escalated
        </p>
      </header>

      <div className="filters">
        <label>
          <span className="sr-only">Filter by review status</span>
          <select
            value={filters.status}
            onChange={(e) => onFilters({ ...filters, status: e.target.value as ReviewStatus | "" })}
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        <label>
          <span className="sr-only">Filter by category</span>
          <select
            value={filters.category}
            onChange={(e) => onFilters({ ...filters, category: e.target.value as Category | "" })}
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c.replace("_", " ")}</option>
            ))}
          </select>
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={filters.escalatedOnly}
            onChange={(e) => onFilters({ ...filters, escalatedOnly: e.target.checked })}
          />
          Escalated only
        </label>

        <button type="button" className="link" onClick={onReload}>
          Reload
        </button>
      </div>

      {loading ? (
        <p className="empty">Loading…</p>
      ) : items.length === 0 ? (
        // An empty result from a filter is not the same as an empty inbox, and
        // saying which is the difference between a usable UI and a confusing one.
        <p className="empty">
          No tickets match these filters.
          <br />
          <button type="button" className="link" onClick={() => onFilters({ status: "", category: "", escalatedOnly: false })}>
            Clear filters
          </button>
        </p>
      ) : (
        <ul className="queue-items">
          {items.map((view) => (
            <QueueItem
              key={view.ticket.id}
              view={view}
              selected={view.ticket.id === selectedId}
              busy={triaging.has(view.ticket.id)}
              onSelect={() => onSelect(view.ticket.id)}
            />
          ))}
        </ul>
      )}
    </aside>
  );
}
