// Shapes of what the API sends. Only the fields the screen uses are listed.

export type StepStatus = "ok" | "warn" | "error";

export interface Step {
  node: string;
  title: string;
  status: StepStatus;
  summary: string;
  duration_ms?: number | null;
  details?: Record<string, unknown> | null;
}

export interface ToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  /** Who decided on the call: the Planner ("plan"), the Retriever model ("agent"), or the web-fallback rule ("fallback"). */
  origin?: string;
  ok?: boolean;
  n_results?: number;
  duration_ms?: number;
  cached?: boolean | null;
  provider?: string | null;
  error?: string | null;
  done: boolean;
}

export interface TableRow {
  rank: number;
  ref: string;
  product: string;
  price: string;
  rating: string;
  ingredients: string;
  source: string;
  citation: string;
  why: string;
  caveat?: string | null;
  url?: string | null;
  image_url?: string | null;
  doc_id?: string | null;
  live_url?: string | null;
  age?: string | null;
  support_refs?: string[];
}

export interface Citation {
  ref: string;
  kind: "catalog" | "web";
  id: string;
  title: string;
  url?: string | null;
  source: string;
  as_of: string;
}

export type AnswerKind =
  | "recommendation" | "abstain" | "clarify" | "chitchat" | "out_of_scope" | "refusal" | "emergency" | "support" | "error";

export interface Answer {
  spoken: string;
  text: string;
  table: TableRow[];
  citations: Citation[];
  notes: string[];
  follow_up_question?: string | null;
  kind: AnswerKind;
  grounded_by?: string | null;
}

export interface Sentence {
  index: number;
  text: string;
  start_s: number;
  end_s: number;
  audio_url: string;
}

export interface Speech {
  text?: string;
  audio_url?: string;
  duration_s?: number;
  speed?: number;
  voice?: string;
  provider?: string;
  max_seconds?: number;
  sentences?: Sentence[];
  dropped_sentences?: string[];
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  confidence?: number | null;
}

export interface Transcript {
  text: string;
  status: "ok" | "low_confidence" | "unclear" | "empty" | "error";
  confidence?: number | null;
  duration_s?: number;
  seconds?: number;
  model?: string;
  provider?: string;
  truncated?: boolean;
  error?: string | null;
  segments?: Segment[];
  usable?: boolean;
}

export interface TurnResult {
  turn_id: string;
  user_text: string;
  answer: Answer;
  trace: Step[];
  speech?: Speech | null;
  asr?: Transcript | null;
  seconds: number;
  cost_usd: number;
  timings_ms: Record<string, number>;
  route?: { intent: string; constraints: Record<string, unknown>; safety?: string } | null;
}

/** One turn as the screen holds it: filled in piece by piece while the events arrive. */
export interface Turn {
  id: string;
  said: string;                  // what the shopper typed, or what was heard
  spokenInput: boolean;
  transcript?: Transcript;
  steps: Step[];
  tools: ToolCall[];
  sentences: Sentence[];
  result?: TurnResult;
  error?: string;
  state: "listening" | "working" | "done" | "failed";
}

export interface Health {
  status: string;
  version: string;
  models: Record<string, string>;
  rule_based: boolean;
  web_search: { providers: string[]; available: boolean; synthetic: boolean; cache_ttl_s: number };
  speech: {
    asr: { provider: string | null; model: string | null } | null;
    tts: { provider: string; voice: string; max_seconds: number } | null;
  };
  mcp: { transport: string; tools: string[] };
  catalog: { documents?: number; brands?: number; embedding_model?: string; error?: string };
  limits: { audio_bytes: number; asr_seconds: number; text_chars: number };
}

export interface ToolSpec {
  name: string;
  alias?: string;
  title?: string | null;
  description?: string;
  input_schema?: { properties?: Record<string, { description?: string; type?: string; default?: unknown }>; required?: string[] };
}

export interface EvalReport {
  name: string;
  kind: "markdown" | "json";
  text?: string;
  data?: Record<string, unknown>;
}

export type StreamEvent =
  | { type: "listening"; turn_id: string }
  | { type: "transcript"; turn_id: string; transcript: Transcript }
  | { type: "start"; turn_id: string; user_text: string }
  | ({ type: "step"; turn_id: string } & Step)
  | ({ type: "tool_call"; turn_id: string } & Omit<ToolCall, "done">)
  | ({ type: "tool_result"; turn_id: string } & Omit<ToolCall, "done">)
  | ({ type: "speech_sentence"; turn_id: string } & Sentence)
  | { type: "result"; turn_id: string; result: TurnResult }
  | { type: "error"; message: string };
