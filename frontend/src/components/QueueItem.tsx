import type { TicketView } from "../api/types";
import { TriageBadges } from "./TriageBadges";

export function QueueItem({
  view,
  selected,
  busy,
  onSelect,
}: {
  view: TicketView;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
}) {
  const { ticket, triage, review } = view;

  return (
    <li>
      <button
        type="button"
        className={`queue-item ${selected ? "selected" : ""} status-${review.status}`}
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
      >
        <div className="queue-item-top">
          <span className="ticket-id">{ticket.id}</span>
          <span className={`status-dot ${review.status}`} title={`Review status: ${review.status}`} />
        </div>

        <div className="subject">{ticket.subject || <em>(no subject)</em>}</div>

        {busy ? (
          <div className="muted small">triaging…</div>
        ) : triage ? (
          <TriageBadges triage={triage} compact />
        ) : (
          // Not an error state: the list serves triage from cache only, so an
          // untriaged ticket simply has not been run yet.
          <div className="muted small">not triaged yet</div>
        )}
      </button>
    </li>
  );
}
