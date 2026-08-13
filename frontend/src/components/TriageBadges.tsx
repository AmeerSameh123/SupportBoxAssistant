import type { Triage } from "../api/types";
import { Badge } from "./ui/Badge";

const CATEGORY_LABELS = {
  billing: "Billing",
  bug: "Bug",
  feature_request: "Feature",
  account: "Account",
  security: "Security",
  other: "Other",
} as const;

export function TriageBadges({
  triage,
  compact = false,
  size = "sm",
}: {
  triage: Triage;
  compact?: boolean;
  size?: "sm" | "lg";
}) {
  const large = size === "lg" ? " is-large" : "";

  return (
    <span className={`signal-chips ${compact ? "is-compact" : ""}`}>
      <Badge className={`signal-chip category-chip category-${triage.category}${large}`}>
        <span className="badge-dot" aria-hidden="true" />
        {CATEGORY_LABELS[triage.category]}
      </Badge>
      <Badge className={`signal-chip priority-chip priority-${triage.priority}${large}`}>
        <span className="badge-dot" aria-hidden="true" />
        {triage.priority}
      </Badge>

      {triage.escalate && (
        <Badge
          tone="primary"
          className={`signal-chip escalation-chip${large}`}
          title={`Escalated because: ${triage.telemetry.escalation_reasons
            .join(", ")
            .replace(/_/g, " ")}`}
        >
          Escalated
        </Badge>
      )}
      {triage.injection_suspected && (
        <Badge
          tone="danger"
          className={`signal-chip critical-chip${large}`}
          title="This message contains instructions aimed at an automated system. They were not followed."
        >
          Injection risk
        </Badge>
      )}
      {triage.spam_suspected && (
        <Badge
          tone="danger"
          className={`signal-chip critical-chip${large}`}
          title="Looks like spam or phishing. No reply has been drafted."
        >
          Spam
        </Badge>
      )}
      {triage.degraded && (
        <Badge
          tone="warning"
          className={`signal-chip degraded-chip${large}`}
          title="The model was unavailable; this came from the keyword fallback."
        >
          Fallback
        </Badge>
      )}
      {!compact && triage.telemetry.repairs.length > 0 && (
        <Badge
          className={`signal-chip repaired-chip${large}`}
          title={`Malformed model output was repaired: ${triage.telemetry.repairs.join(", ")}`}
        >
          Output repaired
        </Badge>
      )}
    </span>
  );
}
