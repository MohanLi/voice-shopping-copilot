import { useState } from "react";

import type { Turn } from "../types";

interface Props {
  turn: Turn;
  busy: boolean;
  onCorrect: (text: string) => void;
}

const STATUS_NOTE: Record<string, string> = {
  low_confidence: "Heard with low confidence. Correct it if it is wrong.",
  unclear: "Too unclear to act on.",
  empty: "No speech was detected.",
  error: "The recording could not be read.",
};

/** The shopper's own words, set large: on a voice turn this is what was heard, with how sure the recogniser was. */
export function Heard({ turn, busy, onCorrect }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const transcript = turn.transcript;

  if (turn.state === "listening" && !turn.said) {
    return <p className="heard heard--pending">Transcribing your recording</p>;
  }
  const said = turn.said || (transcript ? "(nothing heard)" : "");
  const confidence = transcript?.confidence != null ? `${Math.round(transcript.confidence * 100)}% confidence` : null;
  return (
    <div className="heard-block">
      {editing ? (
        <form
          className="heard-edit"
          onSubmit={(event) => {
            event.preventDefault();
            const value = draft.trim();
            setEditing(false);
            if (value && value !== turn.said) onCorrect(value);
          }}
        >
          <label className="visually-hidden" htmlFor="correction">Correct what was heard</label>
          <textarea id="correction" value={draft} rows={2} maxLength={500} autoFocus onChange={(event) => setDraft(event.target.value)} />
          <button type="submit" className="button">Ask again with this</button>
          <button type="button" className="button button--quiet" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <blockquote className={`heard${transcript && !transcript.usable ? " heard--doubt" : ""}`}>{said}</blockquote>
      )}
      {turn.spokenInput && transcript && !editing && (
        <p className="heard-meta">
          {transcript.status === "ok" ? "Heard" : STATUS_NOTE[transcript.status] ?? "Heard"}
          {confidence && transcript.status !== "error" && transcript.status !== "empty" ? `, ${confidence}` : ""}
          {transcript.duration_s ? `, ${transcript.duration_s.toFixed(1)} s of audio` : ""}
          {transcript.model ? `, ${transcript.model}` : ""}
          {transcript.truncated ? ". Only the first part of the recording was used" : ""}.{" "}
          {transcript.segments && transcript.segments.length > 0 && (
            <span className="heard-times">
              {transcript.segments.map((segment) => `${segment.start.toFixed(1)}–${segment.end.toFixed(1)} s`).join(", ")}.{" "}
            </span>
          )}
          {turn.said && (
            <button type="button" className="link" disabled={busy} onClick={() => { setDraft(turn.said); setEditing(true); }}>
              Correct it
            </button>
          )}
        </p>
      )}
    </div>
  );
}
