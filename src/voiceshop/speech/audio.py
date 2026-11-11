"""Audio helpers shared by ASR and TTS: decoding any common format, WAV encoding, silence trimming, fades."""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path
from typing import BinaryIO

import numpy as np

log = logging.getLogger(__name__)
ASR_SAMPLE_RATE = 16000            # what Whisper models expect


class AudioError(ValueError):
    """The audio could not be read, or there is no sound in it."""


def decode_audio(source: str | Path | bytes | BinaryIO, sample_rate: int = ASR_SAMPLE_RATE) -> np.ndarray:
    """Decode a recording to mono float samples in [-1, 1] at ``sample_rate``.

    Anything FFmpeg reads is accepted (WAV, MP3, WebM/Opus from a browser, MP4/AAC, OGG, FLAC),
    given as a path, bytes or an open binary file. Decoding is done here with PyAV rather than
    left to the speech library, so it does not depend on which PyAV release that library was
    written against. A recording that breaks off part-way keeps what was decoded up to there.
    """
    import av

    if isinstance(source, bytes | bytearray):
        if not source:
            raise AudioError("the recording is empty")
        source = io.BytesIO(bytes(source))
    elif isinstance(source, Path):
        source = str(source)
    errors = (getattr(av.error, "FFmpegError", OSError), ValueError, OSError, EOFError, IndexError)
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sample_rate)
    chunks: list[np.ndarray] = []
    try:
        with av.open(source, mode="r") as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise AudioError("the file has no audio track")
            try:
                for frame in container.decode(stream):
                    chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            except errors as exc:
                if not chunks:
                    raise
                log.warning("audio decoding stopped early (%s); keeping the first %.1f s", type(exc).__name__,
                            sum(len(c) for c in chunks) / sample_rate)
            if chunks:
                chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))      # flush
    except AudioError:
        raise
    except errors as exc:
        raise AudioError(f"the audio could not be read ({type(exc).__name__})") from exc
    if not chunks:
        raise AudioError("the recording is empty")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Mono float samples in [-1, 1] -> 16-bit PCM WAV."""
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def read_wav(data: bytes | str | Path) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV -> mono float samples and the sample rate."""
    source = io.BytesIO(data) if isinstance(data, bytes) else str(data)
    with wave.open(source, "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError("only 16-bit PCM WAV is supported here")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def trim_silence(samples: np.ndarray, sample_rate: int, threshold: float = 0.01, keep_s: float = 0.06) -> np.ndarray:
    """Remove leading and trailing near-silence, keeping a short margin so words are not clipped."""
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples
    margin = int(keep_s * sample_rate)
    return samples[max(0, loud[0] - margin): min(len(samples), loud[-1] + margin)]


def cut_with_fade(samples: np.ndarray, sample_rate: int, max_seconds: float, fade_s: float = 0.15) -> np.ndarray:
    """Last resort for the duration limit: cut at ``max_seconds`` with a short fade-out."""
    limit = int(max_seconds * sample_rate)
    if len(samples) <= limit:
        return samples
    out = samples[:limit].copy()
    fade = min(int(fade_s * sample_rate), limit)
    if fade > 0:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out
