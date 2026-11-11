import { type FormEvent, useRef, useState } from "react";

import { useRecorder } from "../useRecorder";

interface Props {
  busy: boolean;
  canListen: boolean;
  maxSeconds: number;
  onText: (text: string) => void;
  onAudio: (audio: Blob, fileName: string) => void;
}

/** Where a request starts: the microphone first, typing and file upload beside it. */
export function AskBar({ busy, canListen, maxSeconds, onText, onAudio }: Props) {
  const [text, setText] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const recorder = useRecorder({ maxSeconds, onDone: onAudio });
  const recording = recorder.state === "recording";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onText(value);
  };

  const micLabel = recording ? "Stop" : recorder.state === "starting" ? "Starting" : "Talk";
  return (
    <section className="ask" aria-label="Ask for a product">
      <button
        type="button"
        className={`mic${recording ? " mic--live" : ""}`}
        style={{ "--level": recorder.level } as React.CSSProperties}
        onClick={() => (recording ? recorder.stop() : void recorder.start())}
        disabled={busy || !canListen || recorder.state === "starting"}
        aria-pressed={recording}
        aria-label={recording ? "Stop recording and send" : "Start recording your request"}
      >
        <svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true">
          {recording
            ? <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" />
            : <><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" /></>}
        </svg>
        <span>{micLabel}</span>
      </button>

      <div className="ask__side">
        <p className="ask__hint" role="status">
          {recording
            ? `Listening, ${recorder.seconds.toFixed(0)} s. It stops by itself when you pause.`
            : !canListen
              ? "Speech recognition is switched off on this server. Type your request."
              : busy ? "Working on it." : "Press Talk and say what you are looking for."}
        </p>
        <form className="ask__form" onSubmit={submit}>
          <label className="visually-hidden" htmlFor="request">Type your request</label>
          <input
            id="request" type="text" value={text} maxLength={500} autoComplete="off" disabled={busy || recording}
            placeholder="or type it: a board game for a ten-year-old, under $30"
            onChange={(event) => setText(event.target.value)}
          />
          <button type="submit" className="button" disabled={busy || recording || !text.trim()}>Ask</button>
          <button type="button" className="button button--quiet" disabled={busy || recording || !canListen} onClick={() => file.current?.click()}>
            Upload a recording
          </button>
          <input
            ref={file} type="file" accept="audio/*,.wav,.mp3,.m4a,.webm,.ogg" hidden
            onChange={(event) => {
              const chosen = event.target.files?.[0];
              event.target.value = "";
              if (chosen) onAudio(chosen, chosen.name);
            }}
          />
        </form>
        {recorder.problem && <p className="problem" role="alert">{recorder.problem}</p>}
      </div>
    </section>
  );
}
