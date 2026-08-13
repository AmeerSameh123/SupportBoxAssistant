import { useState } from "react";
import type { TicketView } from "../api/types";
import { ChevronDown } from "lucide-react";
import * as Collapsible from "@radix-ui/react-collapsible";
import { ConfidenceMeter } from "./ConfidenceMeter";
import { ReplyEditor } from "./ReplyEditor";
import { TriageBadges } from "./TriageBadges";
import { Avatar, AvatarFallback } from "./ui/Avatar";
import { Button } from "./ui/Button";

export function TicketDetail({
  view, busy, onTriage, onSubmit,
}: {
  view: TicketView;
  busy: boolean;
  onTriage: (force: boolean) => void;
  onSubmit: (
    status: "approved" | "rejected",
    reply: string | null,
    note: string | null,
  ) => Promise<void>;
}) {
  const { ticket, triage, review } = view;
  const senderName = ticket.sender.split("@")[0]?.replace(/[._-]+/g, " ") || "Customer";
  const senderInitial = senderName.charAt(0).toUpperCase();
  const [telemetryOpen, setTelemetryOpen] = useState(false);

  return (
    <main className="review-canvas">
      <article className="ticket-workspace">
        <header className="ticket-header">
          <div className="ticket-breadcrumb">
            <span>Inbox</span>
            <span aria-hidden="true">/</span>
            <span className="ticket-code">{ticket.id}</span>
          </div>

          <div className="ticket-title-row">
            <div className="ticket-title-copy">
              <h2>{ticket.subject || <em>No subject</em>}</h2>
              <div className="ticket-origin">
                <Avatar className="detail-avatar" aria-hidden="true">
                  <AvatarFallback>{senderInitial}</AvatarFallback>
                </Avatar>
                <span>
                  <strong>{senderName}</strong>
                  <span>{ticket.sender}</span>
                </span>
              </div>
            </div>

            <span className={`review-status status-${review.status}`}>
              {review.status === "pending" ? "Awaiting review" : review.status}
            </span>
          </div>
        </header>

        <section className="message-section" aria-labelledby="message-heading">
          <div className="section-title-row">
            <div>
              <h3 id="message-heading">Customer message</h3>
            </div>
            <div className="message-meta">
              <span>{new Date(ticket.received_at).toLocaleString()}</span>
              <span>{ticket.channel}</span>
            </div>
          </div>

          <div className="message-content">
            <pre>{ticket.body || "(empty message body)"}</pre>
          </div>
        </section>

        <section className="classification-section" aria-labelledby="classification-heading">
          <div className="section-title-row">
            <div>
              <div>
                <h3 id="classification-heading">Classification analysis</h3>
                <p>Model evidence and deterministic policy signals</p>
              </div>
            </div>
            {triage && (
              <div className="classification-actions">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onTriage(false)}
                  disabled={busy}
                  title="Run the classifier again"
                >
                  {busy && <span className="spinner" />}
                  {busy ? "Running" : "Re-classify"}
                </Button>
                <Button
                  variant="quiet"
                  size="sm"
                  onClick={() => onTriage(true)}
                  disabled={busy}
                  title="Bypass the cache and call the model again from scratch"
                >
                  Force refresh
                </Button>
              </div>
            )}
          </div>

          {busy ? (
            <div className="classification-running" aria-live="polite">
              <span className="classification-loader">
                <span />
              </span>
              <div>
                <strong>Building a fresh classification</strong>
                <span>The local model is reading the message and applying review policy.</span>
              </div>
              <span className="running-pulse" aria-hidden="true"><i /><i /><i /></span>
            </div>
          ) : triage ? (
            <div className="classification-surface">
              <div className="classification-layout">
                <div className="classification-verdict">
                  <TriageBadges triage={triage} size="lg" />
                  <div className="summary-block">
                    <span>Suggested understanding</span>
                    <p>{triage.summary}</p>
                  </div>
                </div>
                <ConfidenceMeter value={triage.confidence} />
              </div>

              {(triage.escalate || triage.injection_suspected || triage.degraded) && (
                <div className="analysis-alerts">
                  {triage.escalate && (
                    <div className="analysis-alert alert-escalation">
                      <div>
                        <strong>Human attention required</strong>
                        <p>
                          {triage.telemetry.escalation_reasons
                            .join(", ")
                            .replace(/_/g, " ")}
                        </p>
                      </div>
                    </div>
                  )}

                  {triage.injection_suspected && (
                    <div className="analysis-alert alert-danger">
                      <div>
                        <strong>Prompt injection detected</strong>
                        <p>
                          The message targets an automated system. Its instructions were isolated
                          and were not followed.
                        </p>
                      </div>
                    </div>
                  )}

                  {triage.degraded && (
                    <div className="analysis-alert alert-warning">
                      <div>
                        <strong>Fallback classification</strong>
                        <p>
                          The model was unreachable. This result came from keyword matching and
                          should be treated as a placeholder.
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              )}

              <Collapsible.Root
                className="analysis-details"
                open={telemetryOpen}
                onOpenChange={setTelemetryOpen}
              >
                <Collapsible.Trigger asChild>
                  <Button variant="quiet" size="sm" className="telemetry-trigger">
                    <ChevronDown
                      size={15}
                      className={telemetryOpen ? "is-open" : ""}
                    />
                    {telemetryOpen ? "Hide telemetry" : "Inspect telemetry"}
                  </Button>
                </Collapsible.Trigger>
                <Collapsible.Content className="telemetry-content">
                  <dl>
                    <div>
                      <dt>Pipeline</dt>
                      <dd>{triage.telemetry.stage}</dd>
                    </div>
                    <div>
                      <dt>Model</dt>
                      <dd>{triage.telemetry.model}</dd>
                    </div>
                    <div>
                      <dt>Prompt</dt>
                      <dd>{triage.telemetry.prompt_version}</dd>
                    </div>
                    <div>
                      <dt>Latency</dt>
                      <dd>{Math.round(triage.telemetry.latency_ms)} ms</dd>
                    </div>
                    <div>
                      <dt>Attempts</dt>
                      <dd>{triage.telemetry.attempts}</dd>
                    </div>
                    <div>
                      <dt>Repairs</dt>
                      <dd>{triage.telemetry.repairs.length > 0 ? triage.telemetry.repairs.join(", ") : "None"}</dd>
                    </div>
                  </dl>
                </Collapsible.Content>
              </Collapsible.Root>
            </div>
          ) : (
            <div className="classification-empty">
              <span className="classification-empty-icon" aria-hidden="true">
                <span />
              </span>
              <div>
                <h4>Ready for its first pass</h4>
                <p>
                  Generate a category, priority, summary, confidence signal, and editable draft
                  without blocking the rest of the queue.
                </p>
              </div>
              <Button variant="primary" size="lg" onClick={() => onTriage(false)}>
                Run classification
              </Button>
            </div>
          )}
        </section>

        <ReplyEditor view={view} busy={busy} onSubmit={onSubmit} />
      </article>
    </main>
  );
}
