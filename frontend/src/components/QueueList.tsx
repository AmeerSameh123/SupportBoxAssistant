import type { Category, ReviewStatus, TicketView } from "../api/types";
import type { BulkState, Filters, SortKey } from "../hooks/useReviewQueue";
import {
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { QueueItem } from "./QueueItem";
import { Button } from "./ui/Button";
import { Progress } from "./ui/Progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/Select";

const CATEGORIES: Category[] = [
  "billing", "bug", "feature_request", "account", "security", "other",
];
const STATUSES: ReviewStatus[] = ["pending", "approved", "rejected"];

export function QueueList({
  items, selectedId, filters, filtersActive, search, sort, loading, triaging, counts, bulk,
  onSelect, onFilters, onSearch, onSort, onReload, onClearFilters,
}: {
  items: TicketView[];
  selectedId: string | null;
  filters: Filters;
  filtersActive: boolean;
  search: string;
  sort: SortKey;
  loading: boolean;
  triaging: Set<string>;
  counts: {
    total: number;
    pending: number;
    approved: number;
    rejected: number;
    escalated: number;
    untriaged: number;
  };
  bulk: BulkState;
  onSelect: (id: string) => void;
  onFilters: (f: Filters) => void;
  onSearch: (v: string) => void;
  onSort: (s: SortKey) => void;
  onReload: () => void;
  onClearFilters: () => void;
}) {
  const pct = bulk.total > 0 ? Math.round((bulk.done / bulk.total) * 100) : 0;

  return (
    <aside className="queue-panel" aria-label="Review queue">
      <div className="queue-overview">
        <div className="queue-heading">
          <div>
            <span className="queue-kicker">Live inbox</span>
            <h1>Review queue</h1>
          </div>
          <span className="queue-total">{counts.total}</span>
          <Button
            variant="ink"
            size="icon"
            className="queue-icon-button"
            onClick={onReload}
            title="Reload the queue from the server"
            aria-label="Reload queue"
          >
            <RefreshCw size={16} />
          </Button>
        </div>

        <div className="queue-metrics" aria-label="Queue summary">
          <Button
            variant="ink"
            size="md"
            className={`queue-metric metric-pending ${filters.status === "pending" ? "is-active" : ""}`}
            onClick={() =>
              onFilters({ ...filters, status: filters.status === "pending" ? "" : "pending" })
            }
            title="Show only tickets nobody has decided on yet"
          >
            <span className="metric-value">{counts.pending}</span>
            <span className="metric-label">Pending</span>
          </Button>
          <Button
            variant="ink"
            size="md"
            className={`queue-metric metric-approved ${filters.status === "approved" ? "is-active" : ""}`}
            onClick={() =>
              onFilters({ ...filters, status: filters.status === "approved" ? "" : "approved" })
            }
            title="Show tickets approved by a reviewer"
          >
            <span className="metric-value">{counts.approved}</span>
            <span className="metric-label">Approved</span>
          </Button>
          <Button
            variant="ink"
            size="md"
            className={`queue-metric metric-rejected ${filters.status === "rejected" ? "is-active" : ""}`}
            onClick={() =>
              onFilters({ ...filters, status: filters.status === "rejected" ? "" : "rejected" })
            }
            title="Show tickets rejected by a reviewer"
          >
            <span className="metric-value">{counts.rejected}</span>
            <span className="metric-label">Rejected</span>
          </Button>
          <Button
            variant="ink"
            size="md"
            className={`queue-metric metric-escalated ${filters.escalatedOnly ? "is-active" : ""}`}
            onClick={() => onFilters({ ...filters, escalatedOnly: !filters.escalatedOnly })}
            title="Show tickets the policy flagged for human attention"
          >
            <span className="metric-value">{counts.escalated}</span>
            <span className="metric-label">Escalated</span>
          </Button>
        </div>

        <div className="queue-search">
          <Search size={16} />
          <input
            id="queue-search"
            type="search"
            value={search}
            placeholder="Search id, subject, sender, body…"
            aria-label="Search tickets"
            onChange={(e) => onSearch(e.target.value)}
          />
          {!search && <kbd aria-hidden="true">/</kbd>}
          {search && (
            <Button
              variant="ink"
              size="icon"
              className="search-clear"
              onClick={() => onSearch("")}
              aria-label="Clear search"
            >
              <X size={14} />
            </Button>
          )}
        </div>
      </div>

      <div className="queue-toolbar">
        <div className="compact-field">
          <Select
            value={filters.status || "all"}
            onValueChange={(value) =>
              onFilters({ ...filters, status: value === "all" ? "" : value as ReviewStatus })
            }
          >
            <SelectTrigger aria-label="Review status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="all">Any status</SelectItem>
              {STATUSES.map((status) => (
                <SelectItem key={status} value={status}>{status}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="compact-field">
          <Select
            value={filters.category || "all"}
            onValueChange={(value) =>
              onFilters({ ...filters, category: value === "all" ? "" : value as Category })
            }
          >
            <SelectTrigger aria-label="Ticket category">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="all">Any category</SelectItem>
              {CATEGORIES.map((category) => (
                <SelectItem key={category} value={category}>
                  {category.replace(/_/g, " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="compact-field sort-field">
          <Select value={sort} onValueChange={(value) => onSort(value as SortKey)}>
            <SelectTrigger aria-label="Sort tickets">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem value="priority">Priority first</SelectItem>
              <SelectItem value="received">Newest first</SelectItem>
              <SelectItem value="id">Ticket ID</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {filtersActive && (
          <div className="active-filter-summary">
            <span>{items.length} of {counts.total}</span>
            <button type="button" onClick={onClearFilters}>
              Reset
            </button>
          </div>
        )}
      </div>

      {bulk.running && (
        <div className="bulk-status" aria-live="polite">
          <div className="bulk-status-copy">
            <span className="spinner" />
            <strong>Classifying queue</strong>
            <span>{bulk.done} of {bulk.total}</span>
          </div>
          <Progress
            className="bulk-progress-track"
            value={pct}
            aria-label="Bulk classification progress"
          />
        </div>
      )}

      <div className="queue-scroll-region">
        {loading ? (
          <ul className="ticket-list">
            {Array.from({ length: 7 }, (_, i) => (
              <li key={i} className="ticket-skeleton" aria-hidden="true">
                <span className="skeleton-avatar" />
                <div>
                  <span className="skeleton-line short" />
                  <span className="skeleton-line" />
                  <span className="skeleton-line medium" />
                </div>
              </li>
            ))}
            <li className="sr-only">Loading the queue…</li>
          </ul>
        ) : items.length === 0 ? (
          <div className="queue-empty-state">
            <span className="empty-list-mark" aria-hidden="true" />
            {filtersActive ? (
              <>
                <strong>No matching tickets</strong>
                <p>Try a broader search or reset the current filters.</p>
                <Button variant="ink" size="sm" onClick={onClearFilters}>
                  Reset filters
                </Button>
              </>
            ) : (
              <>
                <strong>The inbox is empty</strong>
                <p>Check that the backend loaded tickets.json.</p>
              </>
            )}
          </div>
        ) : (
          <ul className="ticket-list">
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
      </div>

      <footer className="queue-footer">
        <span><kbd>J</kbd><kbd>K</kbd> move</span>
        <span><kbd>T</kbd> classify</span>
        <button type="button" onClick={onClearFilters} disabled={!filtersActive}>
          {filtersActive ? "Clear view" : `${items.length} visible`}
        </button>
      </footer>
    </aside>
  );
}
