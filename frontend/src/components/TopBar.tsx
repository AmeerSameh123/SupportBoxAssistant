import type { Readiness } from "../api/types";
import type { SystemState } from "../hooks/useSystemStatus";
import {
  Keyboard,
  Moon,
  Sun,
} from "lucide-react";
import { Button } from "./ui/Button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/Tooltip";

const STATUS_TEXT: Record<SystemState, { label: string; title: string }> = {
  checking: { label: "Checking systems", title: "Contacting the backend" },
  ready: { label: "Model online", title: "The backend is up and the LLM answered" },
  degraded: {
    label: "Fallback mode",
    title:
      "The backend is up but the LLM is unreachable. Triage still works: it falls back to keyword matching, and every result is flagged 'degraded' and escalated.",
  },
  offline: {
    label: "API offline",
    title: "Nothing is answering on the API. Start the backend, then reload.",
  },
};

export function TopBar({
  status,
  info,
  theme,
  untriaged,
  bulkRunning,
  onTriageAll,
  onShowShortcuts,
  onToggleTheme,
}: {
  status: SystemState;
  info: Readiness | null;
  theme: "light" | "dark";
  untriaged: number;
  bulkRunning: boolean;
  onTriageAll: () => void;
  onShowShortcuts: () => void;
  onToggleTheme: () => void;
}) {
  const text = STATUS_TEXT[status];

  return (
    <header className="app-header">
      <div className="brand-lockup">
        <span className="brand-symbol" aria-hidden="true">
          SB
        </span>
        <span className="brand-copy">
          <strong>SupportBox</strong>
          <span>Review workspace</span>
        </span>
      </div>

      <div className="header-context" aria-label="Workspace mode">
        <span className="context-divider" />
        <span className="context-label">Human review</span>
        <span className="safety-note">Nothing is sent automatically</span>
      </div>

      <div className="header-actions">
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              className={`system-status status-${status}`}
              aria-label={`${text.label}. ${text.title}`}
              role="status"
              tabIndex={0}
            >
              <span className="status-dot" aria-hidden="true" />
              <span>{text.label}</span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" align="end" className="system-tooltip">
            <strong>{text.title}</strong>
            {info && (
              <span>
                {info.model} · {info.prompt_version} · {info.tickets_loaded} tickets
              </span>
            )}
          </TooltipContent>
        </Tooltip>

        <Button
          variant="primary"
          size="md"
          onClick={onTriageAll}
          disabled={bulkRunning || untriaged === 0 || status === "offline"}
          title={
            untriaged === 0
              ? "Every ticket already has a classification"
              : `Classify the ${untriaged} ticket(s) that have not been run yet`
          }
        >
          {bulkRunning && <span className="spinner" />}
          {bulkRunning
            ? "Classifying queue"
            : untriaged === 0
              ? "Queue classified"
              : `Classify ${untriaged} remaining`}
        </Button>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ink"
              size="icon"
              onClick={onToggleTheme}
              aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            Switch to {theme === "light" ? "dark" : "light"} mode
          </TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ink"
              size="icon"
              className="shortcut-button"
              onClick={onShowShortcuts}
              aria-label="Keyboard shortcuts"
            >
              <Keyboard size={17} />
              <span className="shortcut-hint">?</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Keyboard commands</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
