/**
 * Confidence, shown with a caveat rather than as a bare number.
 *
 * The eval found this signal to be uninformative on the labelled subset
 * (separation -0.017; see eval/ERROR_ANALYSIS.md). Displaying it as an
 * authoritative percentage would overstate what it means, so the tooltip says so
 * plainly. Honesty in the UI is part of the same job as honesty in the report.
 */
export function ConfidenceMeter({ value, threshold = 0.55 }: { value: number; threshold?: number }) {
  const pct = Math.round(value * 100);
  const level = value < threshold ? "low" : value < 0.8 ? "mid" : "high";

  return (
    <div className="confidence">
      <div className="confidence-head">
        <span className="label">confidence</span>
        <span className={`value ${level}`}>{pct}%</span>
      </div>
      <div
        className="meter"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Model confidence ${pct} percent`}
        title="Self-reported by the model, adjusted by deterministic penalties. Measured as poorly calibrated on the labelled subset - treat as a weak signal."
      >
        <div className={`fill ${level}`} style={{ width: `${pct}%` }} />
        <div className="threshold" style={{ left: `${threshold * 100}%` }} />
      </div>
    </div>
  );
}
