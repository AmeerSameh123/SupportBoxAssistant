import { Progress } from "./ui/Progress";

export function ConfidenceMeter({ value, threshold = 0.55 }: { value: number; threshold?: number }) {
  const pct = Math.round(value * 100);
  const level = value < threshold ? "low" : value < 0.8 ? "mid" : "high";
  const verdict =
    level === "low"
      ? "Review closely"
      : level === "mid"
        ? "Moderate signal"
        : "Stronger signal";

  return (
    <div className={`confidence-instrument confidence-${level}`}>
      <div className="confidence-heading">
        <div>
          <span className="confidence-label" id="confidence-label">
            Confidence
          </span>
          <span className="confidence-verdict">{verdict}</span>
        </div>
        <strong className="confidence-value">{pct}<span>%</span></strong>
      </div>

      <div className="confidence-scale-wrap">
        <Progress
          className="confidence-scale"
          value={pct}
          aria-labelledby="confidence-label"
        />
        <div
          className="confidence-threshold"
          style={{ left: `${threshold * 100}%` }}
          title={`Anything below ${Math.round(threshold * 100)}% is escalated automatically`}
        >
          <span>{Math.round(threshold * 100)}%</span>
        </div>
      </div>

      <div className="confidence-legend" aria-hidden="true">
        <span>Uncertain</span>
        <span>Threshold</span>
        <span>Stronger</span>
      </div>

      <p className="confidence-caveat">
        <span>
          Weakly calibrated. Use this as supporting evidence, never as the decision.
        </span>
      </p>
    </div>
  );
}
