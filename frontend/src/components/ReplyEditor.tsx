import { useEffect, useState } from "react";
import type { TicketView } from "../api/types";

/**
 * Edit the draft, then approve or reject.
 *
 * Two things this deliberately does NOT do:
 *   - send anything to a customer. There is no send endpoint in the API at all;
 *     approving records a decision (PRD 10, API6).
 *   - overwrite the model's original draft. The edit lives on the review record,
 *     so the eval and the audit trail still show what the model actually wrote.
 */
export function ReplyEditor({
  view,
  onSubmit,
}: {
  view: TicketView;
  onSubmit: (status: "approved" | "rejected", reply: string | null, note: string | null) => void;
}) {
  const original = view.triage?.suggested_reply ?? "";
  const [reply, setReply] = useState(view.review.edited_reply ?? original);
  const [note, setNote] = useState(view.review.note ?? "");

  // Re-seed when the selection changes, or after a save returns the saved record.
  useEffect(() => {
    setReply(view.review.edited_reply ?? view.triage?.suggested_reply ?? "");
    setNote(view.review.note ?? "");
  }, [view.ticket.id, view.review.version, view.triage?.suggested_reply, view.review.edited_reply, view.review.note]);

  const dirty = reply !== (view.review.edited_reply ?? original);
  const edited = reply !== original;

  return (
    <section className="reply-editor">
      <div className="section-head">
        <h3>Suggested reply</h3>
        {dirty && <span className="pill warn">unsaved changes</span>}
        {view.triage?.spam_suspected && (
          <span className="pill danger">no reply drafted - do not engage</span>
        )}
      </div>

      <label className="sr-only" htmlFor="reply">Draft reply</label>
      <textarea
        id="reply"
        value={reply}
        rows={8}
        spellCheck
        onChange={(e) => setReply(e.target.value)}
        placeholder={
          view.triage
            ? "No reply was drafted for this ticket."
            : "Run triage to generate a draft."
        }
      />

      {edited && original && (
        <details className="original">
          <summary>Show the model's original draft</summary>
          <p className="muted">{original}</p>
        </details>
      )}

      <label className="sr-only" htmlFor="note">Reviewer note</label>
      <input
        id="note"
        className="note"
        value={note}
        placeholder="Reviewer note (optional) - why you approved or rejected"
        onChange={(e) => setNote(e.target.value)}
      />

      <div className="actions">
        <button
          type="button"
          className="primary"
          onClick={() => onSubmit("approved", reply || null, note || null)}
        >
          Approve
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => onSubmit("rejected", reply || null, note || null)}
        >
          Reject
        </button>
        <span className="muted small">
          Nothing is sent to the customer. This records your decision.
        </span>
      </div>
    </section>
  );
}
