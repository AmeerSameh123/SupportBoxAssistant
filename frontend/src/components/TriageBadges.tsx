import type { Triage } from "../api/types";

/**
 * Category, priority, and the flags a reviewer must not miss.
 *
 * `escalate` carries a tooltip listing the policy reasons that fired.
 * "Escalated" alone tells a reviewer nothing; "escalated: security_category,
 * urgent_priority" tells them what to look at first.
 */
export function TriageBadges({ triage, compact = false }: { triage: Triage; compact?: boolean }) {
  return (
    <div className="badges">
      <span className={`badge cat cat-${triage.category}`}>
        {triage.category.replace("_", " ")}
      </span>
      <span className={`badge pri pri-${triage.priority}`}>{triage.priority}</span>

      {triage.escalate && (
        <span
          className="badge flag escalate"
          title={`Escalated because: ${triage.telemetry.escalation_reasons.join(", ")}`}
        >
          escalate
        </span>
      )}
      {triage.injection_suspected && (
        <span className="badge flag danger" title="Contains instructions aimed at an automated system. Not actioned.">
          injection
        </span>
      )}
      {triage.spam_suspected && (
        <span className="badge flag danger" title="Looks like spam or phishing. No reply has been drafted.">
          spam
        </span>
      )}
      {triage.degraded && (
        <span className="badge flag warn" title="The model was unavailable; this came from the keyword fallback.">
          degraded
        </span>
      )}
      {!compact && triage.telemetry.repairs.length > 0 && (
        <span className="badge flag muted" title={`Malformed model output was repaired: ${triage.telemetry.repairs.join(", ")}`}>
          repaired
        </span>
      )}
      {!compact && <span className="badge stage">{triage.telemetry.stage}</span>}
    </div>
  );
}
