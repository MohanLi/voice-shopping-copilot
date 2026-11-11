import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderState = "idle" | "starting" | "recording";

interface Options {
  maxSeconds: number;
  onDone: (audio: Blob, fileName: string) => void;
}

const SPEECH_LEVEL = 0.045;      // RMS above this counts as speech
const SILENCE_AFTER_SPEECH_MS = 1600;

function pickFormat(): { mimeType?: string; extension: string } {
  const candidates: Array<[string, string]> = [["audio/webm;codecs=opus", "webm"], ["audio/webm", "webm"], ["audio/mp4", "m4a"], ["audio/ogg;codecs=opus", "ogg"]];
  for (const [mimeType, extension] of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(mimeType)) return { mimeType, extension };
  }
  return { extension: "webm" };
}

/**
 * Microphone recording as one fragment. Recording stops when the button is pressed again, when
 * the speaker has been silent for a moment after speaking, or at `maxSeconds`.
 */
export function useRecorder({ maxSeconds, onDone }: Options) {
  const [state, setState] = useState<RecorderState>("idle");
  const [level, setLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const refs = useRef<{ recorder?: MediaRecorder; stream?: MediaStream; context?: AudioContext; frame?: number; cancelled: boolean }>({ cancelled: false });
  const done = useRef(onDone);
  done.current = onDone;

  const release = useCallback(() => {
    const r = refs.current;
    if (r.frame) cancelAnimationFrame(r.frame);
    r.stream?.getTracks().forEach((track) => track.stop());
    void r.context?.close().catch(() => undefined);
    refs.current = { cancelled: false };
    setLevel(0);
  }, []);

  const stop = useCallback(() => {
    const recorder = refs.current.recorder;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }, []);

  const cancel = useCallback(() => {
    refs.current.cancelled = true;
    stop();
  }, [stop]);

  const start = useCallback(async () => {
    if (state !== "idle") return;
    setProblem(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setProblem("This browser cannot record audio. Type your request, or upload a recording.");
      return;
    }
    setState("starting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      setProblem(name === "NotAllowedError"
        ? "The microphone is blocked. Allow it in the browser's site settings, or type your request."
        : "No microphone was found. Type your request, or upload a recording.");
      setState("idle");
      return;
    }
    const format = pickFormat();
    const recorder = new MediaRecorder(stream, format.mimeType ? { mimeType: format.mimeType } : undefined);
    const chunks: Blob[] = [];
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    refs.current = { recorder, stream, context, cancelled: false };

    const startedAt = performance.now();
    let spokeAt = 0;
    let quietSince = 0;
    let lastPaint = 0;
    const watch = (now: number) => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      const rms = Math.sqrt(sum / samples.length);
      if (rms > SPEECH_LEVEL) {
        spokeAt = spokeAt || now;
        quietSince = 0;
      } else if (spokeAt && !quietSince) {
        quietSince = now;
      }
      if (now - lastPaint > 60) {
        lastPaint = now;
        setLevel(Math.min(1, rms * 9));
        setSeconds((now - startedAt) / 1000);
      }
      const longEnough = now - startedAt >= maxSeconds * 1000;
      const finished = spokeAt && quietSince && now - quietSince >= SILENCE_AFTER_SPEECH_MS && now - spokeAt > 500;
      if (longEnough || finished) {
        stop();
        return;
      }
      refs.current.frame = requestAnimationFrame(watch);
    };

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      const cancelled = refs.current.cancelled;
      release();
      setState("idle");
      setSeconds(0);
      if (cancelled) return;
      const audio = new Blob(chunks, { type: recorder.mimeType || format.mimeType || "audio/webm" });
      if (audio.size < 800) {
        setProblem("Nothing was recorded. Press the microphone and speak, then press it again.");
        return;
      }
      done.current(audio, `question.${format.extension}`);
    };
    recorder.start();
    setState("recording");
    refs.current.frame = requestAnimationFrame(watch);
  }, [maxSeconds, release, state, stop]);

  useEffect(() => () => {
    refs.current.cancelled = true;
    const recorder = refs.current.recorder;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    release();
  }, [release]);

  return { state, level, seconds, problem, start, stop, cancel, clearProblem: () => setProblem(null) };
}
