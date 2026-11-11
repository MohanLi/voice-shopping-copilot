import { useState } from "react";

import type { Step, ToolCall, Turn } from "../types";

function took(ms?: number | null): string {
  if (ms == null) return "";
  return ms >= 950 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** A tool argument as a person would read it: {"price_max": 20, "materials": ["wood"]} -> "price_max 20, materials wood". */
function plain(value: unknown): string {
  if (Array.isArray(value)) return value.map(plain).join(" and ");
  if (value && typeof value === "object") return Object.entries(value).map(([key, inner]) => `${key} ${plain(inner)}`).join(", ");
  return String(value);
}

function describeCall(call: ToolCall): string {
  const args = call.arguments ?? {};
  const query = typeof args.query === "string" ? `“${args.query}”` : "";
  const extras = Object.entries(args)
    .filter(([key, value]) => key !== "query" && value != null && !(key === "max_results" || key === "top_k"))
    .map(([key, value]) => `${key}: ${plain(value)}`);
  return [query, ...extras].filter(Boolean).join(", ");
}

function StepLine({ step, calls }: { step: Step; calls: ToolCall[] }) {
  const [open, setOpen] = useState(false);
  const hasDetails = step.details != null && Object.keys(step.details).length > 0;
  return (
    <li className={`step step--${step.status}`}>
      <div className="step__head">
        <span className="step__mark" aria-hidden="true">{step.status === "ok" ? "✓" : step.status === "warn" ? "!" : "×"}</span>
        <span className="step__title">{step.title}</span>
        <span className="step__dots" aria-hidden="true" />
        <span className="step__time">{took(step.duration_ms)}</span>
      </div>
      <p className="step__summary">{step.summary}</p>
      {calls.length > 0 && (
        <ul className="calls">
          {calls.map((call, index) => (
            <li key={index} className={call.done && call.ok === false ? "call call--failed" : "call"}>
              <span className="call__tool">{call.tool}</span> {describeCall(call)}
              <span className="call__result">
                {!call.done ? " running" : call.ok === false ? ` failed: ${call.error ?? "error"}` : ` ${call.n_results ?? 0} result${call.n_results === 1 ? "" : "s"} in ${took(call.duration_ms)}${call.cached ? ", from cache" : ""}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {hasDetails && (
        <>
          <button type="button" className="link step__more" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide details" : "Details"}
          </button>
          {open && <pre className="step__details">{JSON.stringify(step.details, null, 2)}</pre>}
        </>
      )}
    </li>
  );
}

/** How the answer was made: one line per stage with its time, like an itemised receipt, with a total. */
export function Receipt({ turn }: { turn: Turn | undefined }) {
  if (!turn) {
    return (
      <aside className="receipt receipt--empty" aria-label="How the answer was made">
        <h2>How the answer is made</h2>
        <p>Each request passes through speech recognition, a safety check, a router, a planner, a retriever that calls the catalog and the live web, an answerer and a critic. Every step will be listed here with its time.</p>
      </aside>
    );
  }
  const result = turn.result;
  const working = turn.state === "working" || turn.state === "listening";
  return (
    <aside className="receipt" aria-label="How this answer was made">
      <h2>How this answer was made</h2>
      <ol className="steps" aria-live="polite">
        {turn.steps.map((step, index) => (
          <StepLine key={`${step.node}-${index}`} step={step} calls={step.node === "retriever" ? turn.tools : []} />
        ))}
        {working && (
          <li className="step step--running">
            <div className="step__head">
              <span className="step__mark" aria-hidden="true">…</span>
              <span className="step__title">{turn.state === "listening" ? "Speech recognition" : "Working"}</span>
            </div>
            {turn.tools.some((call) => !call.done) && (
              <ul className="calls">
                {turn.tools.filter((call) => !call.done).map((call, index) => (
                  <li key={index} className="call"><span className="call__tool">{call.tool}</span> {describeCall(call)}<span className="call__result"> running</span></li>
                ))}
              </ul>
            )}
          </li>
        )}
      </ol>
      {result && (
        <dl className="total">
          <div><dt>Total time</dt><dd>{result.seconds.toFixed(1)} s</dd></div>
          <div><dt>Model cost</dt><dd>{result.cost_usd > 0 ? `$${result.cost_usd.toFixed(4)}` : "none"}</dd></div>
          <div><dt>Checked by</dt><dd>{result.answer.grounded_by ? result.answer.grounded_by.replace("llm", "model").replace("+", " and ") : "fixed reply"}</dd></div>
        </dl>
      )}
    </aside>
  );
}
