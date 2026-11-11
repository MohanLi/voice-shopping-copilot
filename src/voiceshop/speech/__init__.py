"""Speech in and out: Whisper-class recognition with timestamps, and text to speech held to 15 seconds."""

from voiceshop.speech.asr import Segment, Transcriber, Transcript, build_transcriber
from voiceshop.speech.audio import AudioError, decode_audio
from voiceshop.speech.text import estimate_seconds, normalize_for_speech
from voiceshop.speech.tts import (
    SentenceAudio,
    Speech,
    Synthesizer,
    VoiceOutput,
    build_synthesizer,
    speak_within,
)

__all__ = [
    "AudioError",
    "Segment",
    "SentenceAudio",
    "Speech",
    "Synthesizer",
    "Transcriber",
    "Transcript",
    "VoiceOutput",
    "build_synthesizer",
    "build_transcriber",
    "decode_audio",
    "estimate_seconds",
    "normalize_for_speech",
    "speak_within",
]
