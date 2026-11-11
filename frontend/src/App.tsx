import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getHealth, resetConversation, sendAudio, sendText } from "./api";
import { AskBar } from "./components/AskBar";
import { ComparisonTable } from "./components/ComparisonTable";
import { Drawer, type Panel } from "./components/Drawer";
import { Heard } from "./components/Heard";
import { Receipt } from "./components/Receipt";
import { Reply } from "./components/Reply";
import { Sources } from "./components/Sources";
import type { Health, StreamEvent, Turn } from "./types";
import { useSpeechPlayer } from "./useSpeechPlayer";

const EXAMPLES = [
  "A wooden puzzle for my three-year-old, under $20",
  "Recommend an eco-friendly stainless steel cleaner under $15",
  "What is the current price of the LEGO Classic Creative Suitcase?",
  "A board game for a ten-year-old who likes strategy",
];

function newThreadId(): string {
  return `web-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

/** Fold one stream event into the turn it belongs to. */
function applyEvent(turn: Turn, event: StreamEvent): Turn {
  switch (event.type) {
    case "listening":
      return { ...turn, id: event.turn_id, state: "listening" };
    case "transcript":
      return { ...turn, transcript: event.transcript, said: event.transcript.text, state: "working" };
    case "start":
      return { ...turn, id: event.turn_id, said: turn.said || event.user_text, state: "working" };
    case "step": {
      const { type: _type, turn_id: _turn, ...step } = event;
      return { ...turn, steps: [...turn.steps, step] };
    }
    case "tool_call":
      return { ...turn, tools: [...turn.tools, { tool: event.tool, arguments: event.arguments, done: false }] };
    case "tool_result": {
      const tools = [...turn.tools];
      const key = JSON.stringify(event.arguments);
      const at = tools.findIndex((call) => !call.done && call.tool === event.tool && JSON.stringify(call.arguments) === key);
      const finished = { tool: event.tool, arguments: event.arguments, ok: event.ok, n_results: event.n_results, duration_ms: event.duration_ms,
                         cached: event.cached, provider: event.provider, error: event.error, done: true };
      if (at >= 0) tools[at] = finished; else tools.push(finished);
      return { ...turn, tools };
    }
    case "speech_sentence": {
      const { type: _type, turn_id: _turn, ...sentence } = event;
      return { ...turn, sentences: [...turn.sentences, sentence] };
    }
    case "result":
      return { ...turn, result: event.result, said: turn.said || event.result.user_text, state: "done" };
    case "error":
      return { ...turn, error: event.message, state: "failed" };
  }
}

/** "anthropic:claude-haiku-5-5" -> "Claude Haiku 5.5"; anything else is shown as it is configured. */
function modelName(spec: string): string {
  const id = spec.replace(/^[a-z_]+:/, "");
  const claude = id.match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?/);
  if (claude) return `Claude ${claude[1][0].toUpperCase()}${claude[1].slice(1)} ${claude[2]}${claude[3] ? `.${claude[3]}` : ""}`;
  return id;
}

function statusLine(health: Health): string[] {
  const parts = [health.rule_based ? "Rule-based mode, no language model" : modelName(health.models.answerer ?? "")];
  parts.push(!health.web_search.available ? "live web search off" : health.web_search.synthetic ? "offline sample web data" : `live web search (${health.web_search.providers[0]})`);
  if (health.catalog.documents) parts.push(`${health.catalog.documents.toLocaleString()} catalog products`);
  return parts;
}

export function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const thread = useRef(newThreadId());
  const player = useSpeechPlayer();
  const current = turns[turns.length - 1];
  const earlier = useMemo(() => turns.slice(0, -1).filter((turn) => turn.result), [turns]);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setOffline(true));
  }, []);

  const run = useCallback(async (seed: Turn, send: (onEvent: (event: StreamEvent) => void) => Promise<void>) => {
    player.stop();
    setBusy(true);
    setTurns((all) => [...all, seed]);
    const update = (change: (turn: Turn) => Turn) => setTurns((all) => all.map((turn, index) => (index === all.length - 1 ? change(turn) : turn)));
    try {
      await send((event) => {
        update((turn) => applyEvent(turn, event));
        if (event.type === "speech_sentence") {
          const { type: _type, turn_id: _turn, ...sentence } = event;
          player.enqueue(sentence);
        }
      });
      update((turn) => (turn.state === "done" || turn.state === "failed" ? turn : { ...turn, state: "failed", error: "The connection ended before the answer arrived. Ask again." }));
    } catch (error) {
      const message = error instanceof Error && error.message ? error.message : "The server could not be reached.";
      update((turn) => ({ ...turn, state: "failed", error: /fetch|network/i.test(message) ? "The server could not be reached. Is `voiceshop serve` running?" : message }));
    } finally {
      setBusy(false);
    }
  }, [player]);

  const blank = (said: string, spokenInput: boolean): Turn => ({
    id: `pending-${Date.now()}`, said, spokenInput, steps: [], tools: [], sentences: [], state: spokenInput ? "listening" : "working",
  });
  const askText = useCallback((text: string) => run(blank(text, false), (onEvent) => sendText(text, thread.current, onEvent)), [run]);
  const askAudio = useCallback((audio: Blob, fileName: string) => run(blank("", true), (onEvent) => sendAudio(audio, fileName, thread.current, onEvent)), [run]);

  const startOver = () => {
    player.stop();
    void resetConversation(thread.current);
    thread.current = newThreadId();
    setTurns([]);
  };

  const answer = current?.result?.answer;
  return (
    <div className="page">
      <header className="bar">
        <h1>Voice Shopping Copilot</h1>
        <p className="bar__kind">AI assistant</p>
        <p className="bar__status">
          {offline ? "The server is not answering." : health ? statusLine(health).map((part, index) => <span key={index}>{part}</span>) : "Connecting"}
        </p>
        <nav className="bar__nav" aria-label="More">
          <button type="button" className="link" onClick={() => setPanel("evaluation")}>Evaluation results</button>
          <button type="button" className="link" onClick={() => setPanel("tools")}>Tools</button>
          {turns.length > 0 && <button type="button" className="link" onClick={startOver} disabled={busy}>Start over</button>}
        </nav>
      </header>

      <main className="layout">
        <div className="main">
          {!current && (
            <div className="welcome">
              <p className="heard heard--prompt">What are you shopping for?</p>
              <p className="welcome__sub">Say what it is, who it is for and what you want to spend. You get three options with their sources, and the answer read aloud.</p>
              <p className="welcome__note">You are talking to an AI assistant, not a person. It only searches and compares: to buy, follow the store link beside a product.</p>
            </div>
          )}
          {current && <Heard turn={current} busy={busy} onCorrect={askText} />}

          <AskBar
            busy={busy} canListen={health ? health.speech.asr !== null : true}
            maxSeconds={health?.limits.asr_seconds ?? 30} onText={askText} onAudio={askAudio}
          />

          {!current && (
            <ul className="examples" aria-label="Example requests">
              {EXAMPLES.map((example) => (
                <li key={example}><button type="button" className="example" onClick={() => void askText(example)} disabled={busy}>{example}</button></li>
              ))}
            </ul>
          )}

          {current?.error && <p className="problem" role="alert">{current.error}</p>}
          {current && <Reply turn={current} playing={player.playing} blocked={player.blocked} onPlay={player.play} onStop={player.stop} />}
          {answer && answer.table.length > 0 && <ComparisonTable rows={answer.table} citations={answer.citations} />}
          {answer?.kind === "recommendation" && answer.follow_up_question && !answer.spoken.includes(answer.follow_up_question) && (
            <p className="followup">{answer.follow_up_question}</p>
          )}
          {answer && <Sources answer={answer} />}

          {earlier.length > 0 && (
            <section className="earlier" aria-label="Earlier in this conversation">
              <h2>Earlier in this conversation</h2>
              <ol>
                {earlier.map((turn) => (
                  <li key={turn.id}>
                    <p className="earlier__said">{turn.said}</p>
                    <p className="earlier__reply">{turn.result?.answer.spoken}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>

        <Receipt turn={current} />
      </main>

      {panel && <Drawer panel={panel} onClose={() => setPanel(null)} />}
    </div>
  );
}
