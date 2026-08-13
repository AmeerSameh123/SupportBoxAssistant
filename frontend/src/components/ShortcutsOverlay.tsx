import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./ui/Dialog";

const SHORTCUTS: [string, string][] = [
  ["J", "Move to the next ticket"],
  ["K", "Move to the previous ticket"],
  ["T", "Classify the selected ticket"],
  ["/", "Jump to queue search"],
  ["?", "Open this shortcut guide"],
  ["Esc", "Close the active control"],
];

export function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="shortcut-dialog">
        <div className="shortcut-palette">
          <header>
            <span className="shortcut-palette-icon" aria-hidden="true">
              Keys
            </span>
            <div>
              <DialogTitle id="shortcut-title">Command the inbox</DialogTitle>
              <DialogDescription>
                Move, search, and classify without breaking review focus.
              </DialogDescription>
            </div>
          </header>

          <div className="shortcut-command-line">
            <span>Keyboard layer active</span>
          </div>

          <dl>
            {SHORTCUTS.map(([key, description]) => (
              <div key={key} className="shortcut-row">
                <dt>
                  {key === "Esc" ? <kbd className="wide-key">{key}</kbd> : <kbd>{key}</kbd>}
                </dt>
                <dd>{description}</dd>
              </div>
            ))}
          </dl>

          <footer>
            <span>Commands pause while you type in a field.</span>
            <span><kbd>Esc</kbd> to close</span>
          </footer>
        </div>
      </DialogContent>
    </Dialog>
  );
}
