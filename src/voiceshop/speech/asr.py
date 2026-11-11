"""Speech recognition.

The default is `faster-whisper` (Whisper weights on CTranslate2: no PyTorch, int8 on CPU), run
locally. It accepts whatever the browser or a file gives it (WAV, MP3, WebM/Opus, MP4/AAC),
drops silence with a voice-activity filter, and returns the text with **segment timestamps**,
optional word timestamps, and a confidence value per segment.

Two things are done for this application in particular:

* the recogniser is primed with the catalog's most common brand names, so "Melissa and Doug"
  or "Play-Doh" are spelled the way the catalog spells them;
* every transcript gets a ``status``: ``ok``; ``low_confidence`` (used, but shown as uncertain so
  the shopper can correct it); ``unclear`` or ``empty`` (the assistant asks the shopper to repeat
  instead of searching for a mis-heard phrase); ``error`` (the recording could not be read).

Audio is decoded here (``speech.audio.decode_audio``) and handed to the model as samples.

``ASR_PROVIDER=openai`` switches to OpenAI's hosted transcription behind the same interface.
"""

from __future__ import annotations

import io
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol

import numpy as np

from voiceshop.config import Settings, get_settings
from voiceshop.speech.audio import ASR_SAMPLE_RATE, AudioError, decode_audio

log = logging.getLogger(__name__)
LOCAL_PREFERENCE = ("large-v3-turbo", "medium.en", "small.en", "small", "base.en", "base", "tiny.en", "tiny")
LOW_CONFIDENCE = 0.45              # below this the transcript is used but marked uncertain
REJECT_CONFIDENCE = 0.25           # below this the shopper is asked to repeat
MIN_SECONDS = 0.25                 # shorter recordings are a slipped button, not a request
MAX_WORDS_PER_SECOND = 8.0         # nobody speaks faster; more words than that in a segment were not said
MAX_COMPRESSION_RATIO = 2.4        # Whisper's own threshold for "this text is one phrase repeated"
MAX_NEW_TOKENS = 192               # per 30-second window; bounds the time lost if the decoder starts to loop
Status = Literal["ok", "low_confidence", "unclear", "empty", "error"]
DEFAULT_BRANDS = ("LEGO", "Melissa & Doug", "Play-Doh", "Barbie", "Hot Wheels", "Nerf", "Fisher-Price", "Crayola", "Funko", "Disney",
                  "Playmobil", "Hasbro", "Mattel", "Ravensburger", "VTech", "Schleich")
AudioInput = str | Path | bytes | BinaryIO | np.ndarray


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: float | None = None          # exp(mean token log-probability), 0-1
    no_speech_prob: float | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    text: str
    language: str = "en"
    duration_s: float = 0.0                   # length of the audio
    speech_s: float = 0.0                     # length after silence was removed
    segments: list[Segment] = field(default_factory=list)
    confidence: float | None = None           # duration-weighted mean of the segment confidences
    status: Status = "ok"
    truncated: bool = False                   # the recording was longer than ASR_MAX_SECONDS; only the start was used
    dropped_segments: int = 0                 # segments removed as not physically sayable (see ``plausible``)
    error: str | None = None
    model: str = ""
    provider: str = ""
    seconds: float = 0.0                      # processing time

    @property
    def usable(self) -> bool:
        """There is text to act on (possibly marked uncertain)."""
        return self.status in ("ok", "low_confidence") and bool(self.text)

    @property
    def low_confidence(self) -> bool:
        return self.status in ("low_confidence", "unclear")

    @property
    def real_time_factor(self) -> float | None:
        return round(self.seconds / self.duration_s, 3) if self.duration_s else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(usable=self.usable, real_time_factor=self.real_time_factor)
        return data

    @classmethod
    def failed(cls, message: str, *, model: str = "", provider: str = "") -> Transcript:
        return cls(text="", status="error", error=message, model=model, provider=provider)


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: AudioInput, *, word_timestamps: bool = False) -> Transcript: ...


def _as_source(audio: AudioInput) -> str | BinaryIO:
    if isinstance(audio, bytes | bytearray):
        return io.BytesIO(bytes(audio))
    if isinstance(audio, Path):
        return str(audio)
    return audio  # type: ignore[return-value]


def load_samples(audio: AudioInput, max_seconds: float | None = None) -> tuple[np.ndarray, float, bool]:
    """Samples at 16 kHz, the recording's full length in seconds, and whether it was shortened to ``max_seconds``."""
    samples = np.asarray(audio, dtype=np.float32) if isinstance(audio, np.ndarray) else decode_audio(audio)
    duration = len(samples) / ASR_SAMPLE_RATE
    if max_seconds and duration > max_seconds:
        return samples[: int(max_seconds * ASR_SAMPLE_RATE)], duration, True
    return samples, duration, False


def brand_hint(settings: Settings, limit: int = 40) -> str:
    """The catalog's most common brands, read from the index vocabulary (falls back to a short built-in list)."""
    brands: list[str] = []
    try:
        vocab = json.loads((settings.data_dir / "index" / "vocab.json").read_text())
        top = sorted(vocab.get("brands", {}).items(), key=lambda kv: -kv[1])[:limit]
        brands = [name.title() if name.islower() else name for name, _ in top if 2 < len(name) < 28]
    except (OSError, ValueError):
        pass
    names = list(dict.fromkeys([*DEFAULT_BRANDS, *brands]))[:limit]
    return "A shopper asks a store assistant for a product. Brands mentioned may include " + ", ".join(names) + "."


def plausible(text: str, start: float, end: float, compression_ratio: float | None = None, *, only_segment: bool = False) -> bool:
    """Could this segment have been said in the time it covers?

    Whisper sometimes "continues" after the last real word, most often on a sliver of audio left
    at the end of a clip: a counting sequence, a repeated phrase, a stock sentence. Such text has
    a high token probability, so confidence does not catch it; the clock does.
    """
    words = len(text.split())
    duration = max(end - start, 0.0)
    if compression_ratio is not None and compression_ratio > MAX_COMPRESSION_RATIO and words >= 8:
        return False
    if duration < 0.2 and not only_segment:
        return False
    return words <= max(2.0, duration * MAX_WORDS_PER_SECOND)


def overall_confidence(segments: list[Segment]) -> float | None:
    """Duration-weighted mean of the segment confidences."""
    weighted = [(max(s.end - s.start, 0.05), s.confidence) for s in segments if s.confidence is not None]
    if not weighted:
        return None
    return round(sum(w * c for w, c in weighted) / sum(w for w, _ in weighted), 3)


def assess(text: str, confidence: float | None, *, low: float = LOW_CONFIDENCE, reject: float = REJECT_CONFIDENCE) -> Status:
    """Decide what the assistant should do with a transcript."""
    if not text.strip():
        return "empty"
    if confidence is None:
        return "ok"                                       # the provider gives no confidence; trust the text
    if confidence < reject:
        return "unclear"
    return "low_confidence" if confidence < low else "ok"


class FasterWhisperTranscriber:
    name = "faster_whisper"

    def __init__(self, model: str = "small.en", *, device: str = "auto", compute_type: str = "int8", models_dir: Path | None = None,
                 hint: str | None = None, beam_size: int = 5, max_seconds: float | None = 30.0,
                 low_confidence: float = LOW_CONFIDENCE, reject_confidence: float = REJECT_CONFIDENCE) -> None:
        self.requested_model, self.device, self.compute_type = model, device, compute_type
        self.models_dir, self.hint, self.beam_size = models_dir, hint, beam_size
        self.max_seconds, self.low_confidence, self.reject_confidence = max_seconds, low_confidence, reject_confidence
        self.model_name = model
        self._model: Any = None

    def _resolve(self) -> str:
        """Path of a local copy of the model, a smaller local model if the requested one is absent, or the model id."""
        root = self.models_dir / "whisper" if self.models_dir else None
        if root is not None and (root / self.requested_model / "model.bin").exists():
            return str(root / self.requested_model)
        if root is not None and root.exists():
            for name in LOCAL_PREFERENCE:
                if (root / name / "model.bin").exists():
                    log.warning("ASR model %s is not downloaded; using the local %s instead", self.requested_model, name)
                    self.model_name = name
                    return str(root / name)
        return self.requested_model                      # faster-whisper downloads it from the Hugging Face hub

    def load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            started = time.perf_counter()
            self._model = WhisperModel(self._resolve(), device=self.device, compute_type=self.compute_type)
            log.info("ASR model %s loaded in %.1f s", self.model_name, time.perf_counter() - started)
        return self._model

    def transcribe(self, audio: AudioInput, *, word_timestamps: bool = False) -> Transcript:
        """Never raises for bad audio: an unreadable recording comes back with ``status="error"``."""
        model = self.load()
        started = time.perf_counter()
        try:
            samples, duration, truncated = load_samples(audio, self.max_seconds)
        except AudioError as exc:
            return Transcript.failed(str(exc), model=self.model_name, provider=self.name)
        if duration < MIN_SECONDS:
            return Transcript(text="", status="empty", duration_s=round(duration, 2), model=self.model_name, provider=self.name,
                              seconds=round(time.perf_counter() - started, 3))
        segments_iter, info = model.transcribe(
            samples,
            language="en" if self.model_name.endswith(".en") else None,
            beam_size=self.beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            initial_prompt=self.hint,
            condition_on_previous_text=False,
            word_timestamps=word_timestamps,
            temperature=0.0,                              # one deterministic pass; an unclear result is asked again, not re-guessed
            max_new_tokens=MAX_NEW_TOKENS,
        )
        raw = [seg for seg in segments_iter if seg.text.strip()]      # decoding happens while iterating
        segments: list[Segment] = []
        dropped = 0
        for seg in raw:
            text = seg.text.strip()
            if not plausible(text, seg.start, seg.end, getattr(seg, "compression_ratio", None), only_segment=len(raw) == 1):
                dropped += 1
                log.warning("ASR: dropped an implausible segment [%.2f-%.2f s]: %r", seg.start, seg.end, text[:60])
                continue
            words = [Word(round(w.start, 2), round(w.end, 2), w.word.strip(), round(float(w.probability), 3)) for w in (seg.words or [])]
            segments.append(Segment(
                start=round(seg.start, 2), end=round(seg.end, 2), text=text,
                confidence=round(math.exp(seg.avg_logprob), 3) if seg.avg_logprob is not None else None,
                no_speech_prob=round(float(seg.no_speech_prob), 3) if seg.no_speech_prob is not None else None,
                words=words,
            ))
        text = " ".join(s.text for s in segments).strip()
        confidence = overall_confidence(segments)
        return Transcript(
            text=text,
            language=info.language or "en",
            duration_s=round(duration, 2),
            speech_s=round(float(getattr(info, "duration_after_vad", info.duration) or 0.0), 2),
            segments=segments, confidence=confidence,
            status=assess(text, confidence, low=self.low_confidence, reject=self.reject_confidence), truncated=truncated,
            dropped_segments=dropped, model=self.model_name, provider=self.name, seconds=round(time.perf_counter() - started, 3),
        )


class OpenAITranscriber:
    """OpenAI hosted transcription (``pip install 'voice-shopping-copilot[hosted-voice]'``, ``OPENAI_API_KEY``)."""

    name = "openai"

    def __init__(self, api_key: str | None, model: str = "whisper-1", hint: str | None = None, client: Any = None) -> None:
        self._key, self.model_name, self.hint, self._client = api_key, model, hint, client

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._key:
                raise RuntimeError("ASR_PROVIDER=openai needs OPENAI_API_KEY")
            from openai import OpenAI

            self._client = OpenAI(api_key=self._key)
        return self._client

    def transcribe(self, audio: AudioInput, *, word_timestamps: bool = False) -> Transcript:
        started = time.perf_counter()
        if isinstance(audio, np.ndarray):
            from voiceshop.speech.audio import to_wav_bytes

            audio = to_wav_bytes(audio, ASR_SAMPLE_RATE)
        source = _as_source(audio)
        handle: Any = open(source, "rb") if isinstance(source, str) else source     # noqa: SIM115 - closed below
        if not getattr(handle, "name", None):
            handle.name = "audio.wav"                     # the API infers the format from the file name
        try:
            result = self._get_client().audio.transcriptions.create(
                model=self.model_name, file=handle, response_format="verbose_json", language="en", prompt=self.hint,
                timestamp_granularities=["word", "segment"] if word_timestamps else ["segment"],
            )
        finally:
            if isinstance(source, str):
                handle.close()
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        segments = [Segment(start=round(s.get("start", 0.0), 2), end=round(s.get("end", 0.0), 2), text=str(s.get("text", "")).strip(),
                            confidence=round(math.exp(s["avg_logprob"]), 3) if s.get("avg_logprob") is not None else None,
                            no_speech_prob=s.get("no_speech_prob")) for s in data.get("segments") or []]
        confidence = overall_confidence(segments)
        text = str(data.get("text") or "").strip()
        return Transcript(text=text, language=str(data.get("language") or "en"), duration_s=round(float(data.get("duration") or 0.0), 2),
                          speech_s=round(float(data.get("duration") or 0.0), 2), segments=segments, confidence=confidence,
                          status=assess(text, confidence), model=self.model_name, provider=self.name,
                          seconds=round(time.perf_counter() - started, 3))


def build_transcriber(settings: Settings | None = None) -> Transcriber | None:
    """The configured recogniser, or None when ``ASR_PROVIDER=none``."""
    s = settings or get_settings()
    if s.asr_provider == "none":
        return None
    hint = brand_hint(s)
    if s.asr_provider == "openai":
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
        return OpenAITranscriber(key, model=s.asr_model if s.asr_model.startswith(("whisper", "gpt-")) else "whisper-1", hint=hint)
    return FasterWhisperTranscriber(s.asr_model, device=s.asr_device, compute_type=s.asr_compute_type, models_dir=s.models_dir, hint=hint,
                                    max_seconds=s.asr_max_seconds, low_confidence=s.asr_low_confidence, reject_confidence=s.asr_reject_confidence)
