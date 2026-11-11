import { useCallback, useEffect, useRef, useState } from "react";

import type { Sentence } from "./types";

/**
 * Plays the spoken reply sentence by sentence, in order, starting as soon as the first sentence
 * exists. `playing` is the index of the sentence being spoken (for the captions), or null.
 */
export function useSpeechPlayer() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const queue = useRef<Sentence[]>([]);
  const busy = useRef(false);
  const [playing, setPlaying] = useState<number | null>(null);
  const [blocked, setBlocked] = useState(false);

  const next = useCallback(() => {
    const element = audio.current;
    const sentence = queue.current.shift();
    if (!element || !sentence) {
      busy.current = false;
      setPlaying(null);
      return;
    }
    busy.current = true;
    setPlaying(sentence.index);
    element.src = sentence.audio_url;
    element.play().catch(() => {
      // The browser refused to start audio without a click: offer a button instead of failing silently.
      queue.current.unshift(sentence);
      busy.current = false;
      setPlaying(null);
      setBlocked(true);
    });
  }, []);

  useEffect(() => {
    const element = new Audio();
    element.preload = "auto";
    element.onended = () => next();
    element.onerror = () => next();
    audio.current = element;
    return () => {
      element.pause();
      audio.current = null;
    };
  }, [next]);

  const enqueue = useCallback((sentence: Sentence) => {
    queue.current.push(sentence);
    if (!busy.current) next();
  }, [next]);

  const stop = useCallback(() => {
    queue.current = [];
    busy.current = false;
    audio.current?.pause();
    setPlaying(null);
  }, []);

  const play = useCallback((sentences: Sentence[]) => {
    stop();
    setBlocked(false);
    queue.current = [...sentences];
    next();
  }, [next, stop]);

  return { playing, blocked, enqueue, play, stop };
}
