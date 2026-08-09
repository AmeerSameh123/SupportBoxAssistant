import type { TicketView } from "../api/types";
import { ConfidenceMeter } from "./ConfidenceMeter";
import { ReplyEditor } from "./ReplyEditor";
import { TriageBadges } from "./TriageBadges";

export function TicketDetail({
  view, busy, onTriage, onSubmit,
}: {
  view: TicketView;
  busy: boolean;
  onTriage: (force: boolean) => void;
  onSubmit: (status: "approved" | "rejected", reply: string | null, note: string | null) => void;
}) {
  const { ticket, triage, review } = view;

  return (
    <main className="detail">
      <header className="detail-head">
        <div>
          <h2>{ticket.subject || <em>(no subject)</em>}</h2>
          <p className="muted small">
            {ticket.id} · {ticket.channel} · {ticket.sender} ·{" "}
            {new Date(ticket.received_at).toLocaleString()}
          </p>
        </div>
        <span className={`pill status-${review.status}`}>{review.status}</span>
      </header>

      <section className="original-message">
        <h3>Original message</h3>
        {/* Rendered as text, never as HTML. React escapes by default and there is
            no dangerouslySetInnerHTML anywhere in this app (OWASP LLM02). */}
        <pre className="body">{ticket.body || "(empty)"}</pre>
      </section>

      <section className="triage">
        <div className="section-head">
          <h3>Triage</h3>
          <div className="head-actions">
            <button type="button" className="link" onClick={() => onTriage(false)} disabled={busy}>
              {busy ? "Running…" : triage ? "Re-run" : "Run triage"}
            </button>
            {triage && (
              <button
                type="button"
                className="link"
                onClick={() => onTriage(true)}
                disabled={busy}
                title="Bypass the cache and call the model again"
              >
                Force refresh
              </button>
            )}
          </div>
        </div>

        {busy && !triage ? (
          <p className="muted">Asking the model… this takes a couple of seconds.</p>
        ) : triage ? (
          <>
            <TriageBadges triage={triage} />
            <p className="summary">{triage.summary}</p>
            <ConfidenceMeter value={triage.confidence} />

            {triage.escalate && (
              <p className="callout">
                <strong>Escalated for human attention.</strong>{" "}
                {triage.telemetry.escalation_reasons.join(", ").replace(/_/g, " ")}
              </p>
            )}
            {triage.injection_suspected && (
              <p className="callout danger">
                This message contains instructions aimed at an automated system. It was
                classified normally and <strong>the instructions were not followed</strong>.
              </p>
            )}

            <p className="muted small meta">
              {triage.telemetry.stage} · {triage.telemetry.model} ·{" "}
              {triage.telemetry.prompt_version} · {Math.round(triage.telemetry.latency_ms)} ms
              {triage.telemetry.attempts > 1 && ` · ${triage.telemetry.attempts} attempts`}
              {triage.telemetry.repairs.length > 0 &&
                ` · repaired: ${triage.telemetry.repairs.join(", ")}`}
            </p>
          </>
        ) : (
          <p className="muted">
            Not triaged yet. The queue only shows cached results so it loads instantly.
          </p>
        )}
      </section>

      <ReplyEditor view={view} onSubmit={onSubmit} />
    </main>
  );
}
