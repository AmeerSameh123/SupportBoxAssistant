import { useEffect, useState } from "react";
import type { TicketView } from "../api/types";
import { ChevronDown } from "lucide-react";
import * as Collapsible from "@radix-ui/react-collapsible";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { Textarea } from "./ui/Textarea";

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
  busy,
  onSubmit,
}: {
  view: TicketView;
  busy: boolean;
  onSubmit: (
    status: "approved" | "rejected",
    reply: string | null,
    note: string | null,
  ) => Promise<void>;
}) {
  const original = view.triage?.suggested_reply ?? "";
  const [reply, setReply] = useState(view.review.edited_reply ?? original);
  const [note, setNote] = useState(view.review.note ?? "");
  const [saving, setSaving] = useState<"approved" | "rejected" | null>(null);
  const [originalOpen, setOriginalOpen] = useState(false);

  // Re-seed when the selection changes, or after a save returns the saved record.
  useEffect(() => {
    setReply(view.review.edited_reply ?? view.triage?.suggested_reply ?? "");
    setNote(view.review.note ?? "");
    setSaving(null);
    setOriginalOpen(false);
  }, [
    view.ticket.id,
    view.review.version,
    view.triage?.suggested_reply,
    view.review.edited_reply,
    view.review.note,
  ]);

  const dirty =
    reply !== (view.review.edited_reply ?? original) ||
    note !== (view.review.note ?? "");
  const edited = reply !== original;
  const noTriageYet = !view.triage;
  const wordCount = reply.trim() ? reply.trim().split(/\s+/).length : 0;

  /**
   * The `finally` is the whole point. Clearing `saving` from the re-seed effect
   * alone is not enough: when a save fails, the optimistic update is rolled back
   * to values identical to the ones it started from, so none of that effect's
   * dependencies change and it never fires. Approving a ticket without editing
   * it and then losing the request would leave both buttons disabled on
   * "Saving…" until the reviewer reloaded the page.
   */
  async function submit(status: "approved" | "rejected") {
    setSaving(status);
    try {
      await onSubmit(status, reply || null, note || null);
    } finally {
      setSaving(null);
    }
  }

  return (
    <section className="reply-section" aria-labelledby="reply-heading">
      <div className="section-title-row reply-title-row">
        <div>
          <div>
            <h3 id="reply-heading">Draft and decide</h3>
            <p>Edit the proposed response, then record the human decision.</p>
          </div>
        </div>
        <div className="editor-state">
          {dirty && <span className="editor-badge badge-unsaved">Unsaved changes</span>}
          {view.triage?.spam_suspected && (
            <span className="editor-badge badge-danger">Do not engage</span>
          )}
        </div>
      </div>

      {view.triage?.spam_suspected && (
        <div className="spam-guidance">
          <span>
            The system intentionally left this blank because the message looks like spam or
            phishing. Reject it unless a human review finds a legitimate request.
          </span>
        </div>
      )}

      <div className="editor-frame">
        <div className="editor-label-row">
          <label htmlFor="reply">Reply draft</label>
          <span>{wordCount} words</span>
        </div>
        <Textarea
          id="reply"
          className="reply-textarea"
          value={reply}
          rows={9}
          spellCheck
          disabled={busy}
          onChange={(e) => setReply(e.target.value)}
          placeholder={
            noTriageYet
              ? "Classify this ticket first and the model's draft will appear here."
              : "No reply was drafted. Write one here if this message needs a response."
          }
        />
      </div>

      {edited && original && (
        <Collapsible.Root
          className="original-draft-disclosure"
          open={originalOpen}
          onOpenChange={setOriginalOpen}
        >
          <Collapsible.Trigger asChild>
            <Button variant="quiet" size="sm">
              <ChevronDown size={14} className={originalOpen ? "is-open" : ""} />
              {originalOpen ? "Hide original draft" : "Compare with model original"}
            </Button>
          </Collapsible.Trigger>
          <Collapsible.Content>
            <p>{original}</p>
          </Collapsible.Content>
        </Collapsible.Root>
      )}

      <div className="review-note-field">
        <label htmlFor="note">
          Reviewer note <span>Optional</span>
        </label>
        <Input
          id="note"
          value={note}
          disabled={busy}
          placeholder="Capture context for the next reviewer"
          onChange={(e) => setNote(e.target.value)}
        />
      </div>

      <div className="decision-dock">
        <div className="decision-assurance">
          <span>
            <strong>Decision only</strong>
            Nothing is sent to the customer
          </span>
        </div>

        <div className="decision-actions">
          <Button
            variant="danger"
            size="md"
            disabled={busy || saving !== null}
            onClick={() => submit("rejected")}
          >
            {saving === "rejected" && <span className="spinner" />}
            {saving === "rejected" ? "Recording…" : "Reject"}
          </Button>
          <Button
            variant="approve"
            size="md"
            disabled={busy || saving !== null}
            onClick={() => submit("approved")}
          >
            {saving === "approved" && <span className="spinner" />}
            {saving === "approved" ? "Recording…" : "Approve decision"}
          </Button>
        </div>
      </div>
    </section>
  );
}
