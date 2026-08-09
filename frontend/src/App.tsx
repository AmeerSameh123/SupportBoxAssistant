import { useEffect } from "react";
import { QueueList } from "./components/QueueList";
import { TicketDetail } from "./components/TicketDetail";
import { useReviewQueue } from "./hooks/useReviewQueue";

/** Layout, selection, and keyboard shortcuts. All data logic is in the hook. */
export default function App() {
  const q = useReviewQueue();

  // j/k to move, t to triage. A queue is a keyboard tool; reaching for the mouse
  // 30 times is the difference between "fast first pass" and "another chore".
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      const index = q.items.findIndex((i) => i.ticket.id === q.selectedId);
      if (event.key === "j" && index < q.items.length - 1) {
        q.setSelectedId(q.items[index + 1]!.ticket.id);
      } else if (event.key === "k" && index > 0) {
        q.setSelectedId(q.items[index - 1]!.ticket.id);
      } else if (event.key === "t" && q.selectedId) {
        void q.runTriage(q.selectedId);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [q]);

  return (
    <div className="app">
      {q.error && (
        // aria-live so a screen reader announces failures rather than leaving
        // the reviewer wondering whether the save worked.
        <div className="banner error" role="alert" aria-live="assertive">
          {q.error}
        </div>
      )}

      <div className="columns">
        <QueueList
          items={q.items}
          selectedId={q.selectedId}
          filters={q.filters}
          loading={q.loading}
          triaging={q.triaging}
          counts={q.counts}
          onSelect={q.setSelectedId}
          onFilters={q.setFilters}
          onReload={q.reload}
        />

        {q.selected ? (
          <TicketDetail
            view={q.selected}
            busy={q.triaging.has(q.selected.ticket.id)}
            onTriage={(force) => void q.runTriage(q.selected!.ticket.id, force)}
            onSubmit={(status, reply, note) =>
              void q.submitReview(q.selected!.ticket.id, status, reply, note)
            }
          />
        ) : (
          <main className="detail empty-detail">
            <p className="muted">
              {q.loading ? "Loading the queue…" : "Select a ticket to review."}
            </p>
          </main>
        )}
      </div>

      <footer className="app-footer muted small">
        Human-in-the-loop triage. Nothing is sent to customers. ·{" "}
        <kbd>j</kbd>/<kbd>k</kbd> to move · <kbd>t</kbd> to triage
      </footer>
    </div>
  );
}
