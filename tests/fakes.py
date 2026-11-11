"""Stand-ins for the speech models, shared by the speech and API tests (no model files needed)."""

from __future__ import annotations

from typing import Any

import numpy as np

from voiceshop.speech.asr import Transcript
from voiceshop.speech.text import spoken_words


class FakeVoice:
    """A synthesizer whose audio length is exactly ``seconds_per_word`` per spoken word."""

    name = "fake"
    voice = "test"

    def __init__(self, seconds_per_word: float = 0.33, sample_rate: int = 8000) -> None:
        self.seconds_per_word, self.sample_rate = seconds_per_word, sample_rate
        self.calls: list[tuple[str, float]] = []

    def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        self.calls.append((text, speed))
        n = int(spoken_words(text) * self.seconds_per_word / speed * self.sample_rate)
        return (0.3 * np.sign(np.sin(np.arange(n) * 0.5))).astype(np.float32), self.sample_rate       # loud from the first sample


class FakeEars:
    """A transcriber that returns what it is told to."""

    name = "fake"
    model_name = "fake-ears"

    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.heard: list[Any] = []

    def transcribe(self, audio: Any, *, word_timestamps: bool = False) -> Transcript:
        self.heard.append(audio)
        return self.transcript


def heard(text: str, status: str = "ok", confidence: float = 0.88) -> Transcript:
    return Transcript(text=text, status=status, confidence=confidence, duration_s=4.2, speech_s=3.9, seconds=0.5,  # type: ignore[arg-type]
                      model="fake-ears", provider="fake")
