import { useEffect, useState } from "react";

import { sendFlag } from "../api";
import type { FlagReason } from "../types";

const REASONS: Array<{ reason: FlagReason; label: string }> = [
  { reason: "wrong", label: "Wrong or misleading" },
  { reason: "unsafe", label: "Unsafe" },
  { reason: "other", label: "Something else" },
];

type State = "closed" | "choosing" | "sending" | "sent" | "failed";

/**
 * Nobody checks an answer before it is spoken, so the shopper can say when one is wrong. The flag is
 * stored with the turn's id in the operator's local log, next to the record of how the answer was made.
 */
export function Flag({ turnId }: { turnId: string }) {
  const [state, setState] = useState<State>("closed");
  useEffect(() => setState("closed"), [turnId]);            // a new answer has not been reported

  const report = async (reason: FlagReason) => {
    setState("sending");
    try {
      await sendFlag(turnId, reason);
      setState("sent");
    } catch {
      setState("failed");
    }
  };

  return (
    <section className="flag" aria-label="Report this answer">
      {state === "closed" && (
        <p>
          No person checks an answer before you hear it.{" "}
          <button type="button" className="link" onClick={() => setState("choosing")}>Report this answer</button>
        </p>
      )}
      {(state === "choosing" || state === "sending" || state === "failed") && (
        <>
          <p id="flag-question">What is wrong with this answer?</p>
          <div className="flag__reasons" role="group" aria-labelledby="flag-question">
            {REASONS.map(({ reason, label }) => (
              <button key={reason} type="button" className="button button--quiet" disabled={state === "sending"} onClick={() => void report(reason)}>{label}</button>
            ))}
            <button type="button" className="link" disabled={state === "sending"} onClick={() => setState("closed")}>Cancel</button>
          </div>
          {state === "failed" && <p className="flag__problem" role="alert">The report could not be saved. Try again.</p>}
        </>
      )}
      {state === "sent" && <p role="status">Reported. It is saved with this answer's step log for the operator to review.</p>}
    </section>
  );
}
