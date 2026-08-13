import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { QueueList } from "./components/QueueList";
import { ShortcutsOverlay } from "./components/ShortcutsOverlay";
import { TicketDetail } from "./components/TicketDetail";
import { Toaster } from "./components/Toaster";
import { TopBar } from "./components/TopBar";
import { useReviewQueue } from "./hooks/useReviewQueue";
import { useSystemStatus } from "./hooks/useSystemStatus";
import { useToasts } from "./hooks/useToasts";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const saved = localStorage.getItem("supportbox-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Workspace composition, selection, appearance, and keyboard shortcuts. */
export default function App() {
  const { toast } = useToasts();
  const q = useReviewQueue(toast);
  const { state: systemState, info } = useSystemStatus();
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("supportbox-theme", theme);
  }, [theme]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);

      if (event.key === "Escape" && typing) {
        (target as HTMLInputElement).blur();
        return;
      }
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
      if (showShortcuts) return;

      if (event.key === "/") {
        event.preventDefault();
        document.getElementById("queue-search")?.focus();
        return;
      }
      if (event.key === "?") {
        event.preventDefault();
        setShowShortcuts(true);
        return;
      }

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
  }, [q, showShortcuts]);

  const warned = systemState;
  useEffect(() => {
    if (warned === "offline") {
      toast.error(
        "The API is not answering",
        "Start the backend (uv run uvicorn app.main:app --port 8000), then reload this page.",
      );
    } else if (warned === "degraded") {
      toast.warn(
        "Running without the model",
        "Ollama is unreachable, so classification falls back to keyword matching and every result is flagged degraded. Start Ollama for real results.",
      );
    }
  }, [warned, toast]);

  const onTriage = useCallback(
    (force: boolean) => {
      if (q.selected) void q.runTriage(q.selected.ticket.id, force);
    },
    [q],
  );

  const onSubmit = useCallback(
    async (status: "approved" | "rejected", reply: string | null, note: string | null) => {
      if (q.selected) await q.submitReview(q.selected.ticket.id, status, reply, note);
    },
    [q],
  );

  return (
    <div className="app-shell">
      <TopBar
        status={systemState}
        info={info}
        theme={theme}
        untriaged={q.counts.untriaged}
        bulkRunning={q.bulk.running}
        onTriageAll={() => void q.triageAll()}
        onShowShortcuts={() => setShowShortcuts(true)}
        onToggleTheme={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
      />

      <div className="workspace">
        <QueueList
          items={q.items}
          selectedId={q.selectedId}
          filters={q.filters}
          filtersActive={q.filtersActive}
          search={q.search}
          sort={q.sort}
          loading={q.loading}
          triaging={q.triaging}
          counts={q.counts}
          bulk={q.bulk}
          onSelect={q.setSelectedId}
          onFilters={q.setFilters}
          onSearch={q.setSearch}
          onSort={q.setSort}
          onReload={q.reload}
          onClearFilters={q.clearFilters}
        />

        <AnimatePresence mode="wait" initial={false}>
          {q.selected ? (
            <motion.div
              className="review-transition"
              key={q.selected.ticket.id}
              initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -5, filter: "blur(3px)" }}
              transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            >
              <TicketDetail
                view={q.selected}
                busy={q.triaging.has(q.selected.ticket.id)}
                onTriage={onTriage}
                onSubmit={onSubmit}
              />
            </motion.div>
          ) : (
            <motion.main
              className="review-canvas"
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
            >
              <div className="empty-workspace">
                <span className="empty-illustration" aria-hidden="true">
                  <span className={q.loading ? "empty-orbit is-loading" : "empty-orbit"} />
                </span>
                <span className="empty-kicker">
                  {q.loading ? "Opening workspace" : "Review workspace"}
                </span>
                <h2>{q.loading ? "Bringing your queue into focus…" : "Select a ticket to begin"}</h2>
                <p>
                  {q.loading
                    ? "Reading the latest ticket and review state from the API."
                    : "Choose a message from the queue. Its customer context, model evidence, and draft reply will stay together here."}
                </p>
              </div>
            </motion.main>
          )}
        </AnimatePresence>
      </div>

      <Toaster theme={theme} />
      {showShortcuts && <ShortcutsOverlay onClose={() => setShowShortcuts(false)} />}
    </div>
  );
}
