import type { EvalReport, Health, StreamEvent, ToolSpec } from "./types";

/** Read a server-sent-event response body and hand each event's JSON document to `onEvent`. */
async function readEvents(response: Response, onEvent: (event: StreamEvent) => void): Promise<void> {
  if (!response.ok || !response.body) {
    let detail = `The server answered ${response.status}.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON: keep the status line */
    }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (!data) continue;                                   // a keep-alive comment
      try {
        onEvent(JSON.parse(data) as StreamEvent);
      } catch {
        /* a malformed event is skipped; the stream goes on */
      }
    }
  }
}

export function sendText(text: string, threadId: string, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<void> {
  return fetch("/api/turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, thread_id: threadId }),
    signal,
  }).then((response) => readEvents(response, onEvent));
}

export function sendAudio(audio: Blob, fileName: string, threadId: string, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<void> {
  const form = new FormData();
  form.append("audio", audio, fileName);
  form.append("thread_id", threadId);
  return fetch("/api/voice", { method: "POST", body: form, signal }).then((response) => readEvents(response, onEvent));
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
  return (await response.json()) as T;
}

export const getHealth = () => getJson<Health>("/api/health");
export const getTools = () => getJson<{ transport: string; tools: ToolSpec[] }>("/api/tools");
export const getEvaluation = () => getJson<{ reports: EvalReport[] }>("/api/evaluation");

export function resetConversation(threadId: string): Promise<Response> {
  return fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id: threadId }) });
}
