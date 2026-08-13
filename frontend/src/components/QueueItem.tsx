import type { TicketView } from "../api/types";
import { ChevronRight } from "lucide-react";
import { motion } from "motion/react";
import { TriageBadges } from "./TriageBadges";
import { Avatar, AvatarFallback } from "./ui/Avatar";

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
  const senderName = ticket.sender.split("@")[0]?.replace(/[._-]+/g, " ") || "Customer";
  const senderInitial = senderName.charAt(0).toUpperCase();
  const received = new Date(ticket.received_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

  return (
    <li className="ticket-list-item">
      <motion.button
        type="button"
        className={[
          "ticket-row",
          selected ? "is-selected" : "",
          busy ? "is-busy" : "",
          `is-${review.status}`,
        ].filter(Boolean).join(" ")}
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        whileHover={{ x: selected ? 0 : 3 }}
        whileTap={{ scale: 0.992 }}
        transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
      >
        <Avatar className="sender-avatar" aria-hidden="true">
          <AvatarFallback>{senderInitial}</AvatarFallback>
        </Avatar>

        <span className="ticket-row-content">
          <span className="ticket-row-meta">
            <span className="ticket-id">{ticket.id}</span>
            <span className="sender-name">{senderName}</span>
            <span className="received-date">{received}</span>
          </span>

          <span className="ticket-subject">{ticket.subject || <em>No subject</em>}</span>

          <span className="ticket-row-signals">
            <span className={`queue-review-status ${review.status}`}>
              <span aria-hidden="true" />
              {review.status === "pending" ? "Pending" : review.status}
            </span>
            {busy ? (
              <span className="classification-working">
                <span className="spinner" />
                Classifying
              </span>
            ) : triage ? (
              <TriageBadges triage={triage} compact />
            ) : (
              <span className="needs-classification">
                <span className="signal-dot" aria-hidden="true" />
                Needs classification
              </span>
            )}
          </span>
        </span>

        <span className="ticket-row-state">
          <ChevronRight size={16} className="row-chevron" />
        </span>
      </motion.button>
    </li>
  );
}
