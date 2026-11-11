import type { ReactNode } from "react";

import type { Sentence, Turn } from "../types";

interface Props {
  turn: Turn;
  playing: number | null;
  blocked: boolean;
  onPlay: (sentences: Sentence[]) => void;
  onStop: () => void;
}

const KIND_LABEL: Record<string, string> = {
  emergency: "This may be an emergency",
  support: "You are not alone",
  refusal: "I can't help with that",
  out_of_scope: "Outside what I can help with",
};

/** Phone numbers become links, so that on a phone one tap makes the call. */
function withPhoneLinks(text: string): ReactNode[] {
  const parts = text.split(/(\b1-\d{3}-\d{3}-\d{4}\b|\b(?:911|988)\b)/g);
  return parts.map((part, index) => (index % 2 === 1 ? <a key={index} href={`tel:${part.replace(/-/g, "")}`}>{part}</a> : part));
}

/**
 * The reply as it is written, sentence for sentence; the audio holds the same sentences rewritten
 * for a voice ("$9.99" is said "9 dollars and 99 cents"). When the two line up, the written
 * sentence is shown and highlighted while its audio plays.
 */
function captions(written: string, spoken: Sentence[]): Array<{ index: number; text: string }> {
  const sentences = written.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (spoken.length > 0 && sentences.length >= spoken.length) return sentences.map((text, index) => ({ index, text }));
  return spoken.map((sentence) => ({ index: sentence.index, text: sentence.text }));
}

/** The spoken reply: the words that are said, highlighted sentence by sentence while they play. */
export function Reply({ turn, playing, blocked, onPlay, onStop }: Props) {
  const answer = turn.result?.answer;
  const speech = turn.result?.speech;
  const sentences = speech?.sentences?.length ? speech.sentences : turn.sentences;
  if (!answer && sentences.length === 0) return null;

  const urgent = answer?.kind === "emergency" || answer?.kind === "support";
  const heading = answer ? KIND_LABEL[answer.kind] : undefined;
  const hasAudio = sentences.length > 0;
  const isPlaying = playing !== null;
  const dropped = speech?.dropped_sentences ?? [];
  // An urgent reply shows its full on-screen wording (with the phone number as written); other replies show what is said.
  const written = urgent && answer ? answer.text : answer?.spoken ?? "";
  const lines = urgent ? [] : captions(written, sentences);
  const unsaid = lines.slice(sentences.length);              // written sentences that were left out of the audio
  return (
    <section className={`reply${urgent ? " reply--urgent" : ""}`} aria-label="The assistant's reply" aria-live="polite">
      {heading && <h2 className="reply__kind">{heading}</h2>}
      <div className="reply__row">
        {hasAudio && (
          <button
            type="button" className="play" onClick={() => (isPlaying ? onStop() : onPlay(sentences))}
            aria-label={isPlaying ? "Stop the spoken reply" : "Play the spoken reply"}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              {isPlaying ? <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" /> : <path d="M8 5.5v13l11-6.5z" fill="currentColor" />}
            </svg>
          </button>
        )}
        <p className="reply__text">
          {urgent
            ? withPhoneLinks(written)
            : lines.length > 0
              ? lines.slice(0, sentences.length).map((line) => (
                  <span key={line.index} className={playing === line.index ? "said said--now" : "said"}>{line.text} </span>
                ))
              : written}
          {!urgent && unsaid.length > 0 && <span className="said said--unsaid">{unsaid.map((line) => line.text).join(" ")}</span>}
        </p>
      </div>
      {blocked && <p className="reply__meta">The browser blocked automatic playback. Press play to hear the reply.</p>}
      {speech?.duration_s != null && (
        <p className="reply__meta">
          {speech.duration_s.toFixed(1)} s of speech (the limit is {speech.max_seconds ?? 15} s)
          {speech.speed && speech.speed !== 1 ? `, spoken at ${speech.speed.toFixed(2)}x to fit` : ""}
          {dropped.length > 0 ? ". The grey sentence is shown but not spoken, to stay within the limit" : ""}.
        </p>
      )}
      {answer && !urgent && answer.kind !== "recommendation" && answer.text && answer.text !== answer.spoken && (
        <p className="reply__full">{answer.text}</p>
      )}
    </section>
  );
}
