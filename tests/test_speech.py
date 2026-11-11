"""Speech in and out: text preparation, the 15-second limit, audio decoding, recognition logic, voice turns.

Everything here runs without model files: synthesis and recognition are replaced by small
deterministic stand-ins, so what is tested is this project's logic (what is said, what is cut,
what is asked again), not the models. Tests marked ``models`` use the real Whisper and Kokoro
files and are skipped unless ``--run-models`` is given.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from fakes import FakeEars, FakeVoice
from voiceshop.agents.listen import after_listen, listen_node
from voiceshop.assistant import Assistant
from voiceshop.guardrails import policy
from voiceshop.speech.asr import (
    FasterWhisperTranscriber,
    Segment,
    Transcript,
    assess,
    brand_hint,
    load_samples,
    overall_confidence,
    plausible,
)
from voiceshop.speech.audio import (
    ASR_SAMPLE_RATE,
    AudioError,
    cut_with_fade,
    decode_audio,
    read_wav,
    to_wav_bytes,
    trim_silence,
    wav_duration,
)
from voiceshop.speech.text import (
    MAX_SPEED,
    estimate_seconds,
    fits,
    normalize_for_speech,
    split_sentences,
    spoken_words,
)
from voiceshop.speech.tts import SentenceAudio, VoiceOutput, choose_speed, speak_within

LIMIT = 15.0


# ------------------------------------------------------------------ helpers
def tone(seconds: float, rate: int = 16000, hz: float = 440.0, level: float = 0.3) -> np.ndarray:
    return (level * np.sin(2 * np.pi * hz * np.arange(int(seconds * rate)) / rate)).astype(np.float32)


def encode(samples: np.ndarray, rate: int, container: str, codec: str) -> bytes:
    """Encode with the FFmpeg that ships with PyAV (to test decoding of what browsers and phones produce)."""
    import av

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format=container) as out:
        stream = out.add_stream(codec, rate=rate)
        frame = av.AudioFrame.from_ndarray((samples * 32767).astype("<i2").reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = rate
        resampler = av.audio.resampler.AudioResampler(format=stream.codec_context.format, layout="mono", rate=rate)
        for chunk in (*resampler.resample(frame), *resampler.resample(None)):
            for packet in stream.encode(chunk):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buffer.getvalue()


# ----------------------------------------------------------- text for speech
@pytest.mark.parametrize(("written", "spoken"), [
    ("It is $12.49.", "It is 12 dollars and 49 cents."),
    ("$1,299.00 or $0.99 or $1", "1299 dollars or 99 cents or 1 dollar"),
    ("Melissa & Doug, 3-in-1, 24x36 poster", "Melissa and Doug, 3 in 1, 24 by 36 poster"),
    ("ages 8+ and 2-4 years, 50% off", "ages 8 and up and 2 to 4 years, 50 percent off"),
    ("a 16 oz bottle w/ a 2 ft hose", "a 16 ounces bottle with a 2 feet hose"),
    ("**Top pick** [C1]: see https://example.com/p?id=1 now", "Top pick C1: see now"),
    ("Call Poison Control at 1-800-222-1222, or 911.", "Call Poison Control at 1 800, 2 2 2, 1 2 2 2, or 9 1 1."),
    ("call or text 988 any time", "call or text 9 8 8 any time"),
    ("(312) 555-0199", "3 1 2, 5 5 5, 0 1 9 9"),
    ("the Porsche 911 model, set 10696", "the Porsche 911 model, set 10696"),            # not phone numbers: left alone
    ("part 12-345-67", "part 12 345 67"),                                                # a code, not a range
])
def test_symbols_and_numbers_become_sayable_words(written: str, spoken: str) -> None:
    assert normalize_for_speech(written) == spoken


def test_normalising_twice_changes_nothing() -> None:
    samples = [policy.EMERGENCY, policy.SELF_HARM, *policy.REFUSALS.values(), *policy.FLAG_NOTES.values(),
               "ages 2-4, 3-in-1, 1-800-222-1222, model 911-222-3333-44", "$27.99 & $34.95 — 8+ … 24x36 in.", "", "   "]
    for text in samples:
        once = normalize_for_speech(text)
        assert normalize_for_speech(once) == once


def test_spoken_word_count_reads_numbers_the_way_a_voice_does() -> None:
    assert spoken_words("nine") == 1
    assert spoken_words("$9.99") == 6                    # nine dollars and ninety nine cents
    assert spoken_words("$27.99") == 7                   # twenty seven dollars and ninety nine cents
    assert spoken_words("484 pieces") == 5               # four hundred eighty four pieces
    assert spoken_words("1-800-222-1222") == 10          # one, eight hundred, two two two, one two two two
    assert spoken_words("") == 0


def test_time_estimate_grows_with_text_and_shrinks_with_speed() -> None:
    lead = "My top pick is the Safari chunky puzzle at a catalog price of $9.99."
    longer = lead + " Two more options and the sources are on screen."
    assert 0 < estimate_seconds(lead) < estimate_seconds(longer)
    assert estimate_seconds(longer, 1.2) < estimate_seconds(longer, 1.0)
    assert fits(lead, LIMIT) and not fits(" ".join([lead] * 4), LIMIT, MAX_SPEED)
    assert split_sentences("One. Two! Three?  Four") == ["One.", "Two!", "Three?", "Four"]


def test_every_fixed_reply_can_be_said_within_the_limit_at_normal_speed() -> None:
    replies = [policy.EMERGENCY, policy.SELF_HARM, policy.OUT_OF_SCOPE, *policy.REFUSALS.values(), *policy.LISTEN.values()]
    for reply in replies:
        spoken = policy.spoken_for(reply)
        assert fits(spoken, LIMIT, 1.0), f"too long to say in {LIMIT} s: {spoken}"
    # the numbers that matter are in the spoken wording, in a form a voice reads digit by digit
    assert "1 800, 2 2 2, 1 2 2 2" in normalize_for_speech(policy.spoken_for(policy.EMERGENCY))
    assert "9 1 1" in normalize_for_speech(policy.spoken_for(policy.EMERGENCY))
    assert "9 8 8" in normalize_for_speech(policy.spoken_for(policy.SELF_HARM))


# ------------------------------------------------------ the 15-second limit
LEAD = "My top pick is the Safari chunky puzzle at a catalog price of $9.99."
SAFETY = "It has small parts, so it is not for children under three."
TAIL = "Two more options and the sources are on screen."
QUESTION = "Would you like the most affordable option?"
SHORT = f"{LEAD} {TAIL} {QUESTION}"                    # about 12 seconds
ANSWER = f"{LEAD} {SAFETY} {TAIL} {QUESTION}"          # about 16 seconds at normal speed: needs a slightly faster voice
KOKORO_PACE = 0.326                                    # seconds per word measured for the real voice


def test_an_answer_that_fits_is_spoken_whole_at_normal_speed() -> None:
    voice = FakeVoice(KOKORO_PACE)
    speech = speak_within(voice, SHORT, max_seconds=LIMIT)
    assert speech.speed == 1.0 and not speech.dropped_sentences and not speech.cut
    assert [s["text"] for s in speech.sentences] == split_sentences(normalize_for_speech(SHORT))
    assert speech.duration_s <= LIMIT and abs(speech.duration_s - wav_duration(speech.audio)) < 0.02
    assert len(voice.calls) == 3 and all(speed == 1.0 for _, speed in voice.calls)       # one synthesis per sentence, none repeated


def test_sentences_are_timed_in_order_with_a_pause_between_them() -> None:
    speech = speak_within(FakeVoice(0.30), ANSWER, max_seconds=LIMIT)
    starts = [s["start_s"] for s in speech.sentences]
    ends = [s["end_s"] for s in speech.sentences]
    assert starts[0] == 0.0 and starts == sorted(starts)
    assert all(0.2 <= later - earlier <= 0.3 for earlier, later in zip(ends, starts[1:], strict=False))       # the gap between sentences
    assert ends[-1] == pytest.approx(speech.duration_s, abs=0.02)


def test_a_slightly_long_answer_is_spoken_a_little_faster_and_kept_whole() -> None:
    speech = speak_within(FakeVoice(KOKORO_PACE), ANSWER, max_seconds=LIMIT)
    assert 1.0 < speech.speed <= MAX_SPEED
    assert speech.estimated_s > LIMIT * 0.95                                              # which is why the rate was raised
    assert speech.duration_s <= LIMIT and not speech.cut and not speech.dropped_sentences and len(speech.sentences) == 4


def test_what_does_not_fit_is_dropped_from_the_end_never_from_the_start() -> None:
    voice = FakeVoice(seconds_per_word=0.60)                                              # a much slower voice than estimated
    speech = speak_within(voice, ANSWER, max_seconds=LIMIT)
    sentences = split_sentences(normalize_for_speech(ANSWER))
    kept = [s["text"] for s in speech.sentences]
    assert speech.duration_s <= LIMIT and not speech.cut
    assert kept == sentences[: len(kept)] and speech.dropped_sentences == sentences[len(kept):]
    assert kept[0].startswith("My top pick") and speech.dropped_sentences[-1].startswith("Would you like")
    assert speech.text == " ".join(kept)                                                  # the report says what was actually said


def test_one_sentence_longer_than_the_limit_is_cut_with_a_fade() -> None:
    speech = speak_within(FakeVoice(0.5), "word " * 60 + "end.", max_seconds=5.0)
    assert speech.cut and speech.duration_s == pytest.approx(5.0, abs=0.01)
    samples, _ = read_wav(speech.audio)
    assert abs(samples[-1]) < 0.01 < abs(samples[len(samples) // 2])                      # faded out, not chopped


@pytest.mark.parametrize("seconds_per_word", [0.2, 0.33, 0.45, 0.7, 1.5])
@pytest.mark.parametrize("limit", [4.0, 8.0, 15.0])
def test_the_audio_is_never_longer_than_the_limit(seconds_per_word: float, limit: float) -> None:
    for text in (ANSWER, policy.spoken_for(policy.EMERGENCY), "Yes.", "One sentence only, with a price of $1,299.00 and no end " * 3):
        speech = speak_within(FakeVoice(seconds_per_word), text, max_seconds=limit)
        assert wav_duration(speech.audio) <= limit + 1e-6
        assert speech.sentences, "something is always said"


def test_speaking_rate_is_only_raised_when_needed_and_never_past_the_cap() -> None:
    assert choose_speed("Short and sweet.", max_seconds=LIMIT)[0] == 1.0
    rate, estimate = choose_speed(" ".join(["This sentence has exactly six words."] * 9), max_seconds=LIMIT)
    assert estimate > LIMIT and 1.0 < rate <= MAX_SPEED
    assert choose_speed("word " * 400, max_seconds=LIMIT)[0] == MAX_SPEED


def test_each_sentence_is_handed_over_as_soon_as_it_is_ready(tmp_path: Path, agent_env: Any) -> None:
    order: list[str] = []
    voice = FakeVoice(0.30)
    original = voice.synthesize

    def synthesize(text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        order.append(f"synth:{text[:12]}")
        return original(text, speed)

    voice.synthesize = synthesize  # type: ignore[method-assign]
    got: list[SentenceAudio] = []

    def on_sentence(sentence: SentenceAudio) -> None:
        order.append(f"ready:{sentence.text[:12]}")
        got.append(sentence)

    speech = speak_within(voice, ANSWER, max_seconds=LIMIT, on_sentence=on_sentence)
    assert order[:4] == ["synth:My top pick ", "ready:My top pick ", "synth:It has small", "ready:It has small"]   # not batched at the end
    assert [g.index for g in got] == [0, 1, 2, 3] and got[0].start_s == 0.0
    assert sum(len(g.samples) for g in got) / voice.sample_rate == pytest.approx(speech.duration_s - 3 * 0.25, abs=0.05)
    assert speech.first_audio_s <= speech.seconds


def test_voice_output_keeps_the_audio_and_one_file_per_sentence(agent_env: Any, tmp_path: Path) -> None:
    out = VoiceOutput(FakeVoice(0.30))
    sentences: list[dict[str, Any]] = []
    info = out.speak_sync(ANSWER, turn_id="turn123", on_sentence=sentences.append)
    assert Path(info["audio"]).name == "turn123.wav" and Path(info["audio"]).parent == tmp_path / "logs" / "audio"
    assert wav_duration(Path(info["audio"]).read_bytes()) == pytest.approx(info["duration_s"], abs=0.02)
    assert info["duration_s"] <= info["max_seconds"] == LIMIT
    assert [Path(s["audio"]).name for s in sentences] == [f"turn123_{i}.wav" for i in range(4)]
    assert all(Path(s["audio"]).stat().st_size > 44 for s in sentences)
    assert "audio" not in {k for k, v in info.items() if isinstance(v, bytes)}            # the report carries a path, not the bytes
    json.dumps(info)                                                                      # and is serialisable for the turn log


# -------------------------------------------------------------- audio decode
def test_wav_round_trip_and_helpers() -> None:
    samples = tone(1.0, 24000)
    data = to_wav_bytes(samples, 24000)
    back, rate = read_wav(data)
    assert rate == 24000 and len(back) == len(samples) and np.allclose(back, samples, atol=1e-3)
    assert wav_duration(data) == pytest.approx(1.0)
    padded = np.concatenate([np.zeros(8000, dtype=np.float32), samples, np.zeros(8000, dtype=np.float32)])
    assert len(trim_silence(padded, 24000)) < len(padded) - 12000                          # silence at both ends removed, a margin kept
    assert len(cut_with_fade(samples, 24000, 0.5)) == 12000 and len(cut_with_fade(samples, 24000, 2.0)) == 24000


def test_decoding_accepts_paths_bytes_and_files_and_resamples_to_16_khz(tmp_path: Path) -> None:
    data = to_wav_bytes(tone(1.5, 44100), 44100)
    path = tmp_path / "clip.wav"
    path.write_bytes(data)
    for source in (path, str(path), data, io.BytesIO(data)):
        out = decode_audio(source)
        assert out.dtype == np.float32 and len(out) == pytest.approx(1.5 * ASR_SAMPLE_RATE, abs=64)
        assert 0.25 < float(np.abs(out).max()) < 0.35                                      # level preserved, range [-1, 1]


def test_stereo_is_mixed_down_to_mono() -> None:
    left, right = tone(1.0, 16000, 440), tone(1.0, 16000, 880)
    stereo = np.stack([left, right], axis=1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes((stereo * 32767).astype("<i2").tobytes())
    out = decode_audio(buffer.getvalue())
    assert out.ndim == 1 and len(out) == pytest.approx(16000, abs=32)


@pytest.mark.parametrize(("container", "codec"), [("mp3", "libmp3lame"), ("webm", "libopus"), ("mp4", "aac"), ("flac", "flac")])
def test_decoding_reads_what_browsers_and_phones_record(container: str, codec: str) -> None:
    try:
        blob = encode(tone(2.0, 48000), 48000, container, codec)
    except Exception as exc:                                  # this FFmpeg build cannot write the format: nothing to test with
        pytest.skip(f"cannot encode {container}/{codec} here: {type(exc).__name__}")
    out = decode_audio(blob)
    assert len(out) / ASR_SAMPLE_RATE == pytest.approx(2.0, abs=0.1)
    assert float(np.abs(out).max()) > 0.1


@pytest.mark.parametrize("bad", [b"", b"this is not audio at all " * 20, "/no/such/file.wav"])
def test_unreadable_audio_is_reported_not_raised_as_a_library_error(bad: Any) -> None:
    with pytest.raises(AudioError):
        decode_audio(bad)


# ------------------------------------------------------ recognition logic
def test_transcript_status_from_text_and_confidence() -> None:
    assert assess("a wooden puzzle", 0.9) == "ok"
    assert assess("a wooden puzzle", 0.40) == "low_confidence"
    assert assess("a wooden puzzle", 0.10) == "unclear"
    assert assess("", 0.9) == assess("   ", None) == "empty"
    assert assess("typed by a provider without confidence", None) == "ok"
    assert assess("x", 0.3, low=0.2, reject=0.1) == "ok"                                   # thresholds are configurable
    ok, shaky, lost = (Transcript(text="t", status=s) for s in ("ok", "low_confidence", "unclear"))  # type: ignore[arg-type]
    assert (ok.usable, shaky.usable, lost.usable) == (True, True, False)
    assert (ok.low_confidence, shaky.low_confidence) == (False, True)
    failed = Transcript.failed("the audio could not be read (InvalidDataError)", model="small.en", provider="faster_whisper")
    assert failed.status == "error" and not failed.usable and failed.to_dict()["usable"] is False


def test_confidence_is_weighted_by_how_long_each_segment_lasts() -> None:
    segments = [Segment(0.0, 9.0, "long and clear", confidence=0.9), Segment(9.0, 10.0, "short and unsure", confidence=0.1)]
    assert overall_confidence(segments) == pytest.approx(0.82)
    assert overall_confidence([]) is None and overall_confidence([Segment(0, 1, "no score")]) is None


def test_text_that_could_not_have_been_said_in_the_time_is_implausible() -> None:
    assert plausible("Sorry I didn't catch that, could you say it again?", 0.0, 2.5)
    assert plausible("yes", 0.0, 0.15, only_segment=True)                                  # a real one-word answer
    assert not plausible("1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5", 2.50, 2.66)     # sixteen words in 0.16 s
    assert not plausible("7", 2.50, 2.60)                                                  # a sliver after the real segment
    assert not plausible("thank you " * 12, 0.0, 12.0, compression_ratio=4.1)              # a loop, at a sayable pace
    assert plausible("i said the blue one not the red one the blue one", 0.0, 4.0, compression_ratio=1.4)


def test_long_recordings_are_shortened_and_marked() -> None:
    samples, duration, truncated = load_samples(tone(12.0), max_seconds=5.0)
    assert (len(samples), duration, truncated) == (5 * ASR_SAMPLE_RATE, 12.0, True)
    samples, duration, truncated = load_samples(to_wav_bytes(tone(2.0), ASR_SAMPLE_RATE), max_seconds=5.0)
    assert len(samples) == 2 * ASR_SAMPLE_RATE and not truncated


class FakeWhisper:
    """Stands in for the Whisper model: returns prepared segments and records how it was called."""

    def __init__(self, segments: list[SimpleNamespace]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, samples: np.ndarray, **options: Any) -> tuple[Any, SimpleNamespace]:
        self.calls.append({"samples": len(samples), **options})
        return iter(self.segments), SimpleNamespace(language="en", duration=len(samples) / ASR_SAMPLE_RATE, duration_after_vad=2.4)


def seg(text: str, start: float, end: float, logprob: float = -0.1, ratio: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(text=f" {text}", start=start, end=end, avg_logprob=logprob, no_speech_prob=0.01, compression_ratio=ratio, words=None)


def transcriber_with(segments: list[SimpleNamespace], **options: Any) -> tuple[FasterWhisperTranscriber, FakeWhisper]:
    transcriber = FasterWhisperTranscriber("small.en", hint="Brands may include LEGO.", **options)
    model = FakeWhisper(segments)
    transcriber._model = model
    return transcriber, model


def test_recognition_returns_text_timestamps_and_confidence() -> None:
    transcriber, model = transcriber_with([seg("I need a wooden puzzle", 0.0, 1.4), seg("under twenty dollars.", 1.4, 2.6, logprob=-0.3)])
    result = transcriber.transcribe(to_wav_bytes(tone(2.7), ASR_SAMPLE_RATE))
    assert result.text == "I need a wooden puzzle under twenty dollars." and result.status == "ok" and result.usable
    assert [(s.start, s.end) for s in result.segments] == [(0.0, 1.4), (1.4, 2.6)]
    assert result.segments[0].confidence == pytest.approx(0.905, abs=0.001) and 0.8 < (result.confidence or 0) < 0.9
    assert result.duration_s == pytest.approx(2.7, abs=0.01) and result.speech_s == 2.4 and result.model == "small.en"
    call = model.calls[0]
    assert call["samples"] == pytest.approx(2.7 * ASR_SAMPLE_RATE, abs=64)                 # decoded here, passed as samples
    assert call["initial_prompt"] == "Brands may include LEGO." and call["vad_filter"] is True and call["temperature"] == 0.0


def test_a_hallucinated_tail_is_removed_and_counted() -> None:
    transcriber, _ = transcriber_with([seg("Sorry I didn't catch that, could you say it again?", 0.0, 2.5),
                                       seg("1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5", 2.5, 2.66, ratio=13.9)])
    result = transcriber.transcribe(tone(2.7))
    assert result.text == "Sorry I didn't catch that, could you say it again?"
    assert result.dropped_segments == 1 and len(result.segments) == 1 and result.status == "ok"


def test_unclear_empty_short_and_unreadable_recordings_get_a_status_not_an_exception() -> None:
    mumbled, _ = transcriber_with([seg("a would in puddle", 0.0, 2.0, logprob=-1.8)])
    assert mumbled.transcribe(tone(2.0)).status == "unclear"
    unsure, _ = transcriber_with([seg("a wooden puzzle", 0.0, 2.0, logprob=-0.95)])
    assert unsure.transcribe(tone(2.0)).status == "low_confidence"
    silent, model = transcriber_with([])
    assert silent.transcribe(tone(2.0)).status == "empty"
    assert silent.transcribe(tone(0.1)).status == "empty" and len(model.calls) == 1        # a slipped button never reaches the model
    broken = silent.transcribe(b"not audio")
    assert broken.status == "error" and "could not be read" in (broken.error or "")
    capped, model = transcriber_with([seg("the start of a very long story", 0.0, 3.0)], max_seconds=3.0)
    result = capped.transcribe(tone(10.0))
    assert result.truncated and result.duration_s == 10.0 and model.calls[0]["samples"] == 3 * ASR_SAMPLE_RATE


def test_recogniser_is_primed_with_the_catalog_brands(agent_env: Any, tmp_path: Path) -> None:
    from voiceshop.config import get_settings

    settings = get_settings()
    assert "LEGO" in brand_hint(settings) and "Melissa & Doug" in brand_hint(settings)     # built-in list when there is no index
    index = tmp_path / "data" / "index"
    index.mkdir(parents=True)
    (index / "vocab.json").write_text(json.dumps({"brands": {"carmarace": 40, "zzz obscure": 1, "x": 99}}))
    hint = brand_hint(settings)
    assert "Carmarace" in hint and " X," not in hint and hint.endswith(".")                # catalog spelling; junk names skipped


# -------------------------------------------------------------- listen stage
def heard(text: str, status: str = "ok", **more: Any) -> dict[str, Any]:
    return {"text": text, "status": status, "confidence": 0.9, "duration_s": 3.1, "seconds": 0.42, "model": "small.en", **more}


def test_typed_turns_pass_the_listen_stage_untouched() -> None:
    assert listen_node({"user_text": "a wooden puzzle"}) == {}
    assert after_listen({"user_text": "a wooden puzzle"}) == "guard"


def test_listen_stage_records_what_was_heard() -> None:
    update = listen_node({"asr": heard("a wooden puzzle under twenty dollars")})
    entry = update["trace"][0]
    assert "answer" not in update and entry["node"] == "listen" and entry["status"] == "ok"
    assert 'Heard: "a wooden puzzle under twenty dollars"' in entry["summary"] and entry["duration_ms"] == 420.0
    assert entry["details"]["model"] == "small.en" and entry["details"]["confidence"] == 0.9
    unsure = listen_node({"asr": heard("a wooden puddle", "low_confidence", truncated=True)})["trace"][0]
    assert unsure["status"] == "warn" and "low confidence" in unsure["summary"] and "only the beginning" in unsure["summary"]


@pytest.mark.parametrize(("status", "text"), [("empty", ""), ("unclear", "a would in puddle"), ("error", "")])
def test_listen_stage_ends_the_turn_when_there_is_nothing_to_act_on(status: str, text: str) -> None:
    update = listen_node({"asr": heard(text, status, error="the audio could not be read" if status == "error" else None)})
    answer = update["answer"]
    assert answer.spoken == policy.LISTEN[status] and answer.kind == "clarify" and not answer.table
    assert (text in answer.text) if text else (answer.text == answer.spoken)               # what was heard is shown, not spoken back
    assert update["trace"][0]["status"] == ("error" if status == "error" else "warn")
    assert after_listen({"answer": answer}) == "voice"                                     # straight to the reply: no guard, no model, no search


# ---------------------------------------------------------------- voice turns
@pytest.fixture
async def voice_assistant(mcp_client: Any):  # type: ignore[no-untyped-def]
    """The assistant with stand-in ears and voice: voice turns end to end, offline."""
    bot = Assistant(mcp=mcp_client, speech=VoiceOutput(FakeVoice(0.30)),
                    transcriber=FakeEars(Transcript(text="um i need a wooden puzzle for my three year old under twenty dollars", status="ok",
                                                    confidence=0.88, duration_s=4.2, speech_s=3.9, seconds=0.5, model="small.en", provider="fake")))
    await bot.start()
    try:
        yield bot
    finally:
        await bot.aclose()


async def test_voice_turn_goes_from_recording_to_spoken_answer(voice_assistant: Assistant) -> None:
    result = await voice_assistant.ask_audio(b"<recording>")
    assert voice_assistant.transcriber.heard == [b"<recording>"]
    assert result.asr["text"].startswith("um i need a wooden puzzle") and result.user_text == result.asr["text"]
    assert result.answer.kind == "recommendation" and result.tools_called[0] == "rag.search"
    stages = [entry["node"] for entry in result.trace]
    assert stages[0] == "listen" and stages[-1] == "voice" and {"router", "planner", "retriever", "answerer", "critic"} <= set(stages)
    speech = result.speech
    assert speech["duration_s"] <= LIMIT and Path(speech["audio"]).exists() and speech["provider"] == "fake"
    assert speech["text"] == " ".join(s["text"] for s in speech["sentences"])
    assert normalize_for_speech(result.answer.spoken).startswith(speech["sentences"][0]["text"])        # the audio says the answer
    assert result.timings_ms["listen"] == 500.0 and result.seconds >= 0.5                  # recognition time is part of the turn
    assert "audio" not in (result.to_dict()["speech"] or {})                               # paths stay out of the turn log


async def test_voice_turn_events_arrive_in_the_order_a_screen_needs_them(voice_assistant: Assistant) -> None:
    events = [event async for event in voice_assistant.stream_audio(b"<recording>")]
    kinds = [event["type"] for event in events]
    assert kinds[:3] == ["listening", "transcript", "start"] and kinds[-1] == "result"
    assert len({event["turn_id"] for event in events}) == 1                                # one turn id from the first event to the last
    assert events[1]["transcript"]["usable"] is True
    spoken = [event for event in events if event["type"] == "speech_sentence"]
    assert [event["index"] for event in spoken] == list(range(len(spoken))) and len(spoken) >= 2
    assert all(Path(event["audio"]).exists() for event in spoken)
    first_sentence, voice_step = kinds.index("speech_sentence"), max(i for i, e in enumerate(events) if e.get("node") == "voice")
    assert first_sentence < voice_step                                                     # playable before synthesis has finished
    assert events[-1]["result"].speech["sentences"][0]["text"] == spoken[0]["text"]


@pytest.mark.parametrize("status", ["empty", "unclear", "error"])
async def test_nothing_usable_heard_is_answered_without_agents_and_not_remembered(voice_assistant: Assistant, status: str) -> None:
    voice_assistant.transcriber.transcript = Transcript(text="a would in puddle" if status == "unclear" else "", status=status,  # type: ignore[arg-type]
                                                        confidence=0.1, seconds=0.3, provider="fake")
    result = await voice_assistant.ask_audio(b"<noise>", thread_id="quiet")
    assert result.answer.spoken == policy.LISTEN[status] and result.answer.kind == "clarify"
    assert result.tools_called == [] and result.route is None and result.cost_usd == 0.0
    assert [entry["node"] for entry in result.trace] == ["listen", "voice"]
    assert result.speech["duration_s"] <= LIMIT and result.speech["text"] == normalize_for_speech(policy.LISTEN[status])
    state = (await voice_assistant.graph.aget_state(voice_assistant._config("quiet"))).values
    assert not state.get("history")                                                        # a mis-heard turn leaves no trace in memory


async def test_low_confidence_speech_is_acted_on_and_flagged(voice_assistant: Assistant) -> None:
    voice_assistant.transcriber.transcript = Transcript(text="a wooden puzzle under twenty dollars", status="low_confidence", confidence=0.4,
                                                        seconds=0.3, provider="fake")
    result = await voice_assistant.ask_audio(b"<quiet speech>")
    assert result.answer.kind == "recommendation"
    assert result.trace[0]["node"] == "listen" and result.trace[0]["status"] == "warn"


async def test_emergency_reply_is_spoken_whole_with_the_phone_number(voice_assistant: Assistant) -> None:
    voice_assistant.transcriber.transcript = Transcript(text="my toddler swallowed a button battery what should i do", status="ok", confidence=0.9,
                                                        seconds=0.3, provider="fake")
    result = await voice_assistant.ask_audio(b"<urgent>")
    assert result.answer.text == policy.EMERGENCY and result.answer.spoken == policy.spoken_for(policy.EMERGENCY)
    assert result.tools_called == []
    speech = result.speech
    assert speech["dropped_sentences"] == [] and not speech["cut"] and speech["speed"] == 1.0 and speech["duration_s"] <= LIMIT
    assert "1 800, 2 2 2, 1 2 2 2" in speech["text"] and "9 1 1" in speech["text"]


async def test_typed_turns_are_spoken_too_and_text_only_mode_still_works(voice_assistant: Assistant, assistant: Assistant) -> None:
    spoken = await voice_assistant.ask("a wooden puzzle under twenty dollars")
    assert spoken.asr is None and spoken.trace[0]["node"] != "listen" and spoken.speech["duration_s"] <= LIMIT
    silent = await assistant.ask("a wooden puzzle under twenty dollars")                   # built without speech
    assert silent.speech == {"text": silent.answer.spoken, "audio": None}
    with pytest.raises(RuntimeError, match="Speech recognition is off"):
        await assistant.ask_audio(b"<recording>")


async def test_a_recogniser_that_breaks_ends_the_turn_politely(voice_assistant: Assistant) -> None:
    def boom(audio: Any, *, word_timestamps: bool = False) -> Transcript:
        raise OSError("model file is missing")

    voice_assistant.transcriber.transcribe = boom  # type: ignore[method-assign]
    result = await voice_assistant.ask_audio(b"<recording>")
    assert result.answer.spoken == policy.LISTEN["error"] and result.asr["status"] == "error"
    assert "unavailable (OSError)" in result.asr["error"]


# ------------------------------------------------- with the real model files
@pytest.mark.models
def test_real_voice_and_real_ears_agree_and_respect_the_limit(tmp_path: Path) -> None:
    """Kokoro says a request, Whisper hears it back; a full answer stays within 15 seconds of real audio."""
    from voiceshop.config import get_settings
    from voiceshop.speech import build_synthesizer, build_transcriber

    settings = get_settings()
    voice, ears = build_synthesizer(settings), build_transcriber(settings)
    assert voice is not None and ears is not None
    request = speak_within(voice, "I need a wooden puzzle for my three year old, under twenty dollars.", max_seconds=LIMIT)
    transcript = ears.transcribe(request.audio)
    assert transcript.status == "ok" and transcript.segments and transcript.segments[0].end > 0
    text = transcript.text.lower()
    assert "wooden puzzle" in text and ("twenty dollars" in text or "$20" in text) and ("three year old" in text or "3 year old" in text or "three-year-old" in text or "3-year-old" in text)
    answer = speak_within(voice, ANSWER + " It also comes in a gift box, which is nice for birthdays and holidays.", max_seconds=LIMIT)
    assert wav_duration(answer.audio) <= LIMIT and answer.sentences
    assert ears.transcribe(tone(3.0, level=0.0)).status == "empty"                         # silence is not turned into words
