"""Text to speech, with the spoken summary held to ``TTS_MAX_SECONDS`` (15 s).

The default voice is Kokoro-82M on ONNX Runtime, run locally (no key, works offline).
``TTS_PROVIDER=openai`` or ``elevenlabs`` use hosted voices behind the same interface.

The answer is spoken **sentence by sentence**. That does two things:

* the first sentence can be played while the rest is still being synthesized, so the time to
  first audio is one sentence, not the whole answer;
* the length limit is enforced on the audio actually produced, as it is produced:

  1. the text is normalised for speech and its duration estimated; if the estimate is over the
     limit, the speaking rate is raised (never beyond ``MAX_SPEED``);
  2. each sentence is added only if the total stays within the limit; the first one that would
     not fit is left out together with everything after it. The answer is written most
     important first (recommendation, safety sentence, pointer to the screen, question), so
     what is left out is what matters least, and it is still on screen;
  3. only if the very first sentence alone is too long is the audio cut, with a short fade.

What was done is reported with the audio (``speed``, ``sentences``, ``dropped_sentences``, ``cut``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from voiceshop.config import Settings, get_settings
from voiceshop.speech.audio import cut_with_fade, read_wav, to_wav_bytes, trim_silence
from voiceshop.speech.text import (
    MAX_SPEED,
    SENTENCE_GAP_S,
    estimate_seconds,
    normalize_for_speech,
    split_sentences,
)

log = logging.getLogger(__name__)
SAFETY_MARGIN = 0.95               # aim a little under the limit


@dataclass
class Speech:
    audio: bytes                    # 16-bit mono WAV
    sample_rate: int
    duration_s: float
    text: str                       # what was actually spoken (after normalisation and any shortening)
    voice: str
    speed: float
    provider: str
    seconds: float = 0.0            # synthesis time for the whole answer
    first_audio_s: float = 0.0      # synthesis time until the first sentence could be played
    estimated_s: float = 0.0        # what the text was expected to take at the requested speed
    sentences: list[dict[str, Any]] = field(default_factory=list)      # {"text", "start_s", "end_s"} for each spoken sentence
    dropped_sentences: list[str] = field(default_factory=list)
    cut: bool = False

    def info(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("audio")
        data["real_time_factor"] = round(self.seconds / self.duration_s, 3) if self.duration_s else None
        return data


@dataclass
class SentenceAudio:
    """One synthesized sentence, handed to ``on_sentence`` as soon as it exists."""

    index: int
    text: str
    samples: np.ndarray
    sample_rate: int
    start_s: float
    end_s: float

    def wav(self) -> bytes:
        return to_wav_bytes(self.samples, self.sample_rate)


class Synthesizer(Protocol):
    name: str
    voice: str

    def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        """Return mono float samples in [-1, 1] and the sample rate."""
        ...


class KokoroSynthesizer:
    name = "kokoro"

    def __init__(self, voice: str = "af_heart", models_dir: Path | None = None, lang: str = "en-us") -> None:
        self.voice, self.lang = voice, lang
        self.models_dir = models_dir
        self._engine: Any = None

    def _files(self) -> tuple[Path, Path]:
        root = (self.models_dir or get_settings().models_dir) / "kokoro"
        model = next((p for p in (root / "kokoro-v1.0.int8.onnx", root / "kokoro-v1.0.onnx", root / "kokoro-v1.0.fp16.onnx") if p.exists()), None)
        voices = root / "voices-v1.0.bin"
        if model is None or not voices.exists():
            raise FileNotFoundError(f"Kokoro model files not found in {root}. Run:  python scripts/download_assets.py --only kokoro")
        return model, voices

    def load(self) -> Any:
        if self._engine is None:
            from kokoro_onnx import Kokoro

            model, voices = self._files()
            started = time.perf_counter()
            self._engine = Kokoro(str(model), str(voices))
            log.info("TTS model %s loaded in %.1f s", model.name, time.perf_counter() - started)
        return self._engine

    def voices(self) -> list[str]:
        return sorted(self.load().get_voices())

    def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        samples, rate = self.load().create(text, voice=self.voice, speed=speed, lang=self.lang)
        return np.asarray(samples, dtype=np.float32), int(rate)


class OpenAISynthesizer:
    """OpenAI text-to-speech (``OPENAI_API_KEY``; voices such as alloy, nova, shimmer)."""

    name = "openai"

    def __init__(self, api_key: str | None, voice: str = "alloy", model: str = "gpt-4o-mini-tts", client: Any = None) -> None:
        self._key, self.voice, self.model, self._client = api_key, voice, model, client

    def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        if self._client is None:
            if not self._key:
                raise RuntimeError("TTS_PROVIDER=openai needs OPENAI_API_KEY")
            from openai import OpenAI

            self._client = OpenAI(api_key=self._key)
        response = self._client.audio.speech.create(model=self.model, voice=self.voice, input=text, response_format="wav", speed=speed)
        data = response.read() if hasattr(response, "read") else response.content
        return read_wav(data)


class ElevenLabsSynthesizer:
    """ElevenLabs text-to-speech over HTTPS (``ELEVENLABS_API_KEY``; ``TTS_VOICE`` is a voice id)."""

    name = "elevenlabs"
    SAMPLE_RATE = 24000

    def __init__(self, api_key: str | None, voice: str, model: str = "eleven_turbo_v2_5", client: Any = None) -> None:
        self._key, self.voice, self.model, self._client = api_key, voice, model, client

    def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        import httpx

        if not self._key:
            raise RuntimeError("TTS_PROVIDER=elevenlabs needs ELEVENLABS_API_KEY")
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}",
                params={"output_format": f"pcm_{self.SAMPLE_RATE}"},
                headers={"xi-api-key": self._key, "Content-Type": "application/json"},
                json={"text": text, "model_id": self.model, "voice_settings": {"speed": round(min(max(speed, 0.7), 1.2), 2)}},
            )
            response.raise_for_status()
        finally:
            if self._client is None:
                client.close()
        return np.frombuffer(response.content, dtype="<i2").astype(np.float32) / 32768.0, self.SAMPLE_RATE


def build_synthesizer(settings: Settings | None = None) -> Synthesizer | None:
    s = settings or get_settings()
    if s.tts_provider == "none":
        return None
    if s.tts_provider == "openai":
        return OpenAISynthesizer(s.openai_api_key.get_secret_value() if s.openai_api_key else None,
                                 voice=s.tts_voice if not s.tts_voice.startswith(("af_", "am_", "bf_", "bm_")) else "alloy")
    if s.tts_provider == "elevenlabs":
        return ElevenLabsSynthesizer(s.elevenlabs_api_key.get_secret_value() if s.elevenlabs_api_key else None, voice=s.tts_voice)
    return KokoroSynthesizer(voice=s.tts_voice, models_dir=s.models_dir)


def choose_speed(text: str, *, max_seconds: float, speed: float = 1.0) -> tuple[float, float]:
    """The speaking rate for ``text`` (already normalised) and the estimate it is based on."""
    estimate = estimate_seconds(text, speed)
    target = max_seconds * SAFETY_MARGIN
    if estimate <= target:
        return speed, estimate
    return min(MAX_SPEED, max(speed, round(speed * estimate / target + 0.005, 2))), estimate


def speak_within(synth: Synthesizer, text: str, *, max_seconds: float = 15.0, speed: float = 1.0,
                 on_sentence: Callable[[SentenceAudio], None] | None = None) -> Speech:
    """Synthesize ``text`` sentence by sentence so that the audio is no longer than ``max_seconds``."""
    started = time.perf_counter()
    sentences = split_sentences(normalize_for_speech(text)) or [normalize_for_speech(text)]
    rate, estimate = choose_speed(" ".join(sentences), max_seconds=max_seconds, speed=speed)
    pieces: list[np.ndarray] = []
    spoken: list[dict[str, Any]] = []
    dropped: list[str] = []
    position = 0.0
    sample_rate = 24000
    first_audio = 0.0
    cut = False
    for index, sentence in enumerate(sentences):
        samples, sample_rate = synth.synthesize(sentence, rate)
        samples = trim_silence(samples, sample_rate)
        gap = SENTENCE_GAP_S if index else 0.0
        duration = len(samples) / sample_rate
        if position + gap + duration > max_seconds:
            if index:
                dropped = sentences[index:]              # this sentence and everything after it stay on screen only
                break
            samples = cut_with_fade(samples, sample_rate, max_seconds)       # a single sentence longer than the limit
            duration, cut, dropped = len(samples) / sample_rate, True, sentences[1:]
        if gap:
            pieces.append(np.zeros(int(gap * sample_rate), dtype=np.float32))
        pieces.append(samples)
        start = position + gap
        position = start + duration
        spoken.append({"text": sentence, "start_s": round(start, 2), "end_s": round(position, 2)})
        if index == 0:
            first_audio = time.perf_counter() - started
        if on_sentence is not None:
            on_sentence(SentenceAudio(index, sentence, samples, sample_rate, round(start, 2), round(position, 2)))
        if cut:
            break
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    return Speech(audio=to_wav_bytes(audio, sample_rate), sample_rate=sample_rate, duration_s=round(len(audio) / sample_rate, 2),
                  text=" ".join(item["text"] for item in spoken), voice=synth.voice, speed=rate, provider=synth.name,
                  seconds=round(time.perf_counter() - started, 3), first_audio_s=round(first_audio, 3), estimated_s=round(estimate, 2),
                  sentences=spoken, dropped_sentences=dropped, cut=cut)


class VoiceOutput:
    """What the graph's ``voice`` node uses: speak the answer, keep the audio file, report how it went."""

    def __init__(self, synthesizer: Synthesizer, settings: Settings | None = None) -> None:
        self.synthesizer = synthesizer
        self._settings = settings
        self._lock = threading.Lock()                    # one synthesis at a time on the local model

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    @property
    def audio_dir(self) -> Path:
        return self.settings.logs_dir / "audio"

    def speak_sync(self, text: str, turn_id: str = "", on_sentence: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Speak ``text``; the audio is kept in ``logs/audio/<turn_id>.wav``.

        With ``on_sentence``, each sentence is also written to its own file as soon as it is
        ready and reported (``index``, ``text``, ``start_s``, ``end_s``, ``audio``), so a client
        can start playing the first sentence while the others are synthesized.
        """
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        name = turn_id or str(int(time.time() * 1000))

        def relay(sentence: SentenceAudio) -> None:
            path = self.audio_dir / f"{name}_{sentence.index}.wav"
            path.write_bytes(sentence.wav())
            assert on_sentence is not None
            on_sentence({"index": sentence.index, "text": sentence.text, "start_s": sentence.start_s, "end_s": sentence.end_s, "audio": str(path)})

        with self._lock:
            speech = speak_within(self.synthesizer, text, max_seconds=self.settings.tts_max_seconds, speed=self.settings.tts_speed,
                                  on_sentence=relay if on_sentence is not None else None)
        path = self.audio_dir / f"{name}.wav"
        path.write_bytes(speech.audio)
        return {**speech.info(), "audio": str(path), "max_seconds": self.settings.tts_max_seconds}

    async def speak(self, text: str, turn_id: str = "", on_sentence: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.speak_sync, text, turn_id, on_sentence)
