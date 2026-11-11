"""Speech evaluation: does recognition survive accents and speech variations, and does the spoken reply keep its time limit?

**Accents.** There is no budget for recording dozens of speakers, so the test set is synthetic and says so:
each request is read by Kokoro voices that were trained on speakers of other languages (Spanish, French,
Hindi, Italian, Japanese, Portuguese, Mandarin) reading English, next to American and British voices. That
gives non-native prosody and timbre, not the full range of human accents. Recordings of real people can be
added: put ``name.wav`` and ``name.txt`` (what was said) into ``eval/datasets/voice_samples/`` and they are
scored as their own group.

What is measured per group, besides word error rate, is what matters to this assistant: whether the
**budget**, the **age** and the **brand** survive recognition, since those become filters. Two further
conditions are run on the American voices: fast speech and added noise. The brand hint given to the
recogniser is tested by running brand requests with and without it.

**Time limit.** A set of spoken replies of different lengths, up to far too long, is synthesized with the
15-second limit; the report gives the measured length of each and how the limit was met.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np

from voiceshop.agents import rules
from voiceshop.config import Settings, get_settings
from voiceshop.paths import project_root
from voiceshop.speech.asr import FasterWhisperTranscriber, brand_hint
from voiceshop.speech.audio import decode_audio, read_wav, to_wav_bytes, trim_silence
from voiceshop.speech.text import estimate_seconds, normalize_for_speech
from voiceshop.speech.tts import KokoroSynthesizer, speak_within

log = logging.getLogger(__name__)

# (group, voice prefix, phonemiser language). Voices are taken from what the installed voice file offers.
VOICE_GROUPS: list[tuple[str, str, str]] = [
    ("American English", "af_", "en-us"), ("American English", "am_", "en-us"),
    ("British English", "bf_", "en-gb"), ("British English", "bm_", "en-gb"),
    ("Spanish-accented", "ef_", "en-us"), ("Spanish-accented", "em_", "en-us"),
    ("French-accented", "ff_", "en-us"),
    ("Hindi-accented", "hf_", "en-us"), ("Hindi-accented", "hm_", "en-us"),
    ("Italian-accented", "if_", "en-us"), ("Italian-accented", "im_", "en-us"),
    ("Japanese-accented", "jf_", "en-us"), ("Japanese-accented", "jm_", "en-us"),
    ("Portuguese-accented", "pf_", "en-us"), ("Portuguese-accented", "pm_", "en-us"),
    ("Mandarin-accented", "zf_", "en-us"), ("Mandarin-accented", "zm_", "en-us"),
]
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}

REPLIES = [
    "Sorry, I didn't catch that. Could you say it again?",
    "My top pick is the Safari chunky puzzle at a catalog price of $9.99. Two more options and the sources are on screen.",
    "My top pick is the Melissa and Doug Safari chunky puzzle, eight wooden pieces for ages two to four, at a catalog price of $9.99. "
    "Two more options and the sources are on screen. Would you like the most affordable option?",
    "My top pick is the EcoShine stainless steel cleaner, $9.49 from Example Home, stated to be plant-based. Follow the label and never mix "
    "cleaning products. One more option and the sources are on screen.",
    "This could be an emergency. Call Poison Control at 1-800-222-1222 right away, or 911 if the person has trouble breathing or is not responding.",
    "The LEGO Classic Medium Creative Brick Box is $27.99 in the catalog, and one store lists it now at $34.95, which is about 25 percent more. "
    "It has 484 pieces in 35 colours and is recommended for ages 4 to 99. It has small parts, so it is not for children under three. "
    "Two more options and the sources are on screen. Would you like me to check another store?",
    "I could not find a stainless steel cleaner under $15.00 in the catalog or on the web. You could try a higher budget. "
    "Would you like me to search with a higher budget or a different product?",
    "word " * 70 + "end.",
]


def normalise(text: str) -> str:
    """Compare what was said with what was heard, not how numbers and punctuation were written."""
    low = normalize_for_speech(text).lower().replace("-", " ")
    words = re.findall(r"[a-z0-9']+", low)
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _TENS:
            value = _TENS[w]
            if i + 1 < len(words) and words[i + 1] in _ONES and 0 < _ONES[words[i + 1]] < 10:
                value += _ONES[words[i + 1]]
                i += 1
            out.append(str(value))
        elif w in _ONES:
            out.append(str(_ONES[w]))
        elif w not in ("um", "uh", "and"):
            out.append(w.replace("'", ""))
        i += 1
    return " ".join(out)


def word_error_rate(reference: str, heard: str) -> float:
    import jiwer

    ref, hyp = normalise(reference), normalise(heard)
    if not ref:
        return 0.0 if not hyp else 1.0
    return float(jiwer.wer(ref, hyp)) if hyp else 1.0


def slots(text: str) -> tuple[float | None, float | None]:
    c = rules.extract_constraints(text)
    return c.budget_max, c.age_years


def brand_heard(brand: str, heard: str) -> bool:
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and"))      # noqa: E731
    return squash(brand) in squash(heard)


def add_noise(samples: np.ndarray, snr_db: float, seed: int = 7) -> np.ndarray:
    power = float(np.mean(samples**2)) or 1e-9
    noise = np.random.default_rng(seed).normal(0.0, np.sqrt(power / (10 ** (snr_db / 10))), len(samples))
    return np.clip(samples + noise, -1.0, 1.0).astype(np.float32)


def _clip(synth: KokoroSynthesizer, voice: str, lang: str, text: str, speed: float, cache: Path) -> np.ndarray:
    """A request read by one voice, at 16 kHz (cached on disk: synthesis is the slow part)."""
    key = hashlib.sha1(f"{voice}|{lang}|{speed}|{text}".encode()).hexdigest()[:16]
    path = cache / f"{voice}_{key}.wav"
    if path.exists():
        return decode_audio(path)
    samples, rate = synth.load().create(normalize_for_speech(text), voice=voice, speed=speed, lang=lang)
    path.write_bytes(to_wav_bytes(trim_silence(np.asarray(samples, dtype=np.float32), rate, keep_s=0.25), rate))
    return decode_audio(path)


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def pct(key: str) -> float | None:
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(100 * sum(values) / len(values), 1) if values else None

    return {"clips": len(rows), "wer_pct": round(100 * statistics.fmean(r["wer"] for r in rows), 1), "budget_ok_pct": pct("budget_ok"),
            "age_ok_pct": pct("age_ok"), "brand_ok_pct": pct("brand_ok"), "usable_pct": pct("usable"),
            "confidence": round(statistics.fmean(r["confidence"] for r in rows if r["confidence"] is not None), 3) if any(r["confidence"] is not None for r in rows) else None}


def run_asr(settings: Settings | None = None, *, utterances: int | None = None, voices_per_prefix: int = 1, out_dir: Path | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    out_dir = out_dir or project_root() / "eval" / "results" / "speech"
    cache = s.data_dir / "cache" / "speech_eval"
    cache.mkdir(parents=True, exist_ok=True)
    items = [json.loads(line) for line in (project_root() / "eval" / "datasets" / "speech_utterances.jsonl").read_text().splitlines() if line.strip()]
    items = items[:utterances] if utterances else items
    synth = KokoroSynthesizer(models_dir=s.models_dir)
    available = synth.voices()
    ears = FasterWhisperTranscriber(s.asr_model, device=s.asr_device, compute_type=s.asr_compute_type, models_dir=s.models_dir, hint=brand_hint(s))
    ears.load()
    plain = FasterWhisperTranscriber(s.asr_model, models_dir=s.models_dir, hint=None)
    plain._model, plain.model_name = ears._model, ears.model_name                    # the same model, without the brand hint

    def judge(item: dict[str, Any], audio: np.ndarray, recogniser: FasterWhisperTranscriber) -> dict[str, Any]:
        heard = recogniser.transcribe(audio)
        want_budget, want_age = slots(item["text"])
        got_budget, got_age = slots(heard.text)
        return {"id": item["id"], "heard": heard.text, "wer": word_error_rate(item["text"], heard.text), "confidence": heard.confidence,
                "usable": heard.usable, "budget_ok": (got_budget == want_budget) if want_budget is not None else None,
                "age_ok": (got_age == want_age) if want_age is not None else None,
                "brand_ok": brand_heard(item["brand"], heard.text) if item.get("brand") else None, "asr_seconds": heard.seconds, "audio_s": heard.duration_s}

    started = time.time()
    rows: list[dict[str, Any]] = []
    for group, prefix, lang in VOICE_GROUPS:
        preferred = [v for v in ("af_heart", "am_michael", "bf_emma", "bm_george") if v.startswith(prefix) and v in available]
        voices = (preferred + [v for v in available if v.startswith(prefix) and v not in preferred])[:voices_per_prefix]
        for voice in voices:
            for item in items:
                audio = _clip(synth, voice, lang, item["text"], 1.0, cache)
                rows.append({"group": group, "voice": voice, "condition": "clean", **judge(item, audio, ears)})
                if item.get("brand"):
                    rows.append({"group": group, "voice": voice, "condition": "no brand hint", **judge(item, audio, plain)})
                if group == "American English":
                    rows.append({"group": group, "voice": voice, "condition": "fast (1.25x)", **judge(item, _clip(synth, voice, lang, item["text"], 1.25, cache), ears)})
                    rows.append({"group": group, "voice": voice, "condition": "noisy (10 dB SNR)", **judge(item, add_noise(audio, 10.0), ears)})
            log.info("speech eval: %s done (%d rows, %.0f s)", voice, len(rows), time.time() - started)

    samples_dir = project_root() / "eval" / "datasets" / "voice_samples"
    for wav in sorted(samples_dir.glob("*.wav")) if samples_dir.exists() else []:
        text_file = wav.with_suffix(".txt")
        if text_file.exists():
            item = {"id": wav.stem, "text": text_file.read_text().strip()}
            rows.append({"group": "Recorded people", "voice": wav.stem, "condition": "clean", **judge(item, decode_audio(wav), ears)})

    clean = [r for r in rows if r["condition"] == "clean"]
    by_group = {g: _score([r for r in clean if r["group"] == g]) for g in dict.fromkeys(r["group"] for r in clean)}
    conditions = {c: _score([r for r in rows if r["condition"] == c and r["group"] == "American English"]) for c in ("clean", "fast (1.25x)", "noisy (10 dB SNR)")
                  if any(r["condition"] == c for r in rows)}
    branded = {r["id"] for r in rows if r["condition"] == "no brand hint"}
    hint = {"with the brand hint": _score([r for r in clean if r["id"] in branded]), "without it": _score([r for r in rows if r["condition"] == "no brand hint"])} if branded else {}
    audio_s, asr_s = sum(r["audio_s"] for r in rows), sum(r["asr_seconds"] for r in rows)
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "asr_model": ears.model_name, "utterances": len(items), "clips": len(rows),
              "voices": sorted({r["voice"] for r in rows}), "overall_clean": _score(clean) if clean else {}, "by_group": by_group, "conditions": conditions,
              "brand_hint": hint, "real_time_factor": round(asr_s / audio_s, 3) if audio_s else None, "seconds": round(time.time() - started, 1),
              "worst": sorted(({k: r[k] for k in ("group", "voice", "condition", "id", "heard", "wer")} for r in rows if r["wer"] > 0.3), key=lambda r: -r["wer"])[:15]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "asr_accents.json").write_text(json.dumps(report, indent=1))
    (out_dir / "asr_accents.md").write_text(asr_markdown(report))
    return report


def _row(name: str, m: dict[str, Any]) -> str:
    show = lambda v: "-" if v is None else f"{v}"      # noqa: E731
    return f"| {name} | {m['clips']} | {show(m['wer_pct'])} | {show(m['budget_ok_pct'])} | {show(m['age_ok_pct'])} | {show(m['brand_ok_pct'])} | {show(m['usable_pct'])} | {show(m['confidence'])} |"


def asr_markdown(report: dict[str, Any]) -> str:
    head = "| {} | clips | WER % | budget kept % | age kept % | brand kept % | usable % | confidence |\n|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [f"Speech recognition on synthetic accents: {report['utterances']} shopping requests read by {len(report['voices'])} voices "
             f"({report['clips']} clips), recognised by Whisper {report['asr_model']} (real-time factor {report['real_time_factor']}). "
             "The voices were trained on speakers of other languages and read English here; they are a stand-in for accents, not a sample of people. "
             "\"Kept\" means the value extracted from the transcript equals the value extracted from the text that was read.", "",
             head.format("Voice group"), *(_row(g, m) for g, m in report["by_group"].items())]
    if report.get("overall_clean"):
        lines.append(_row("All groups", report["overall_clean"]))
    if report.get("conditions"):
        lines += ["", "Speech variations (American voices):", "", head.format("Condition"), *(_row(c, m) for c, m in report["conditions"].items())]
    if report.get("brand_hint"):
        lines += ["", "Requests that name a brand, with and without the catalog's brand names given to the recogniser:", "", head.format("Recogniser"),
                  *(_row(c, m) for c, m in report["brand_hint"].items())]
    return "\n".join(lines) + "\n"


def run_tts(settings: Settings | None = None, *, out_dir: Path | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    out_dir = out_dir or project_root() / "eval" / "results" / "speech"
    synth = KokoroSynthesizer(voice=s.tts_voice, models_dir=s.models_dir)
    synth.load()
    rows = []
    for text in REPLIES:
        speech = speak_within(synth, text, max_seconds=s.tts_max_seconds, speed=s.tts_speed)
        samples, rate = read_wav(speech.audio)
        actual = len(samples) / rate
        said = estimate_seconds(speech.text, speech.speed)
        rows.append({"reply": text[:60] + ("..." if len(text) > 60 else ""), "estimated_s": speech.estimated_s, "audio_s": round(actual, 2), "speed": speech.speed,
                     "sentences_spoken": len(speech.sentences), "sentences_left_out": len(speech.dropped_sentences), "cut": speech.cut,
                     "within_limit": actual <= s.tts_max_seconds + 1e-6, "estimate_error_pct": round(100 * (said - actual) / actual, 1) if actual else None,
                     "first_sentence_ready_s": speech.first_audio_s, "synthesis_s": speech.seconds})
    errors = [abs(r["estimate_error_pct"]) for r in rows if r["estimate_error_pct"] is not None and not r["cut"]]
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "voice": synth.voice, "limit_s": s.tts_max_seconds, "replies": len(rows),
              "within_limit_pct": round(100 * sum(r["within_limit"] for r in rows) / len(rows), 1), "longest_s": max(r["audio_s"] for r in rows),
              "mean_abs_estimate_error_pct": round(statistics.fmean(errors), 1) if errors else None,
              "real_time_factor": round(sum(r["synthesis_s"] for r in rows) / sum(r["audio_s"] for r in rows), 3), "rows": rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tts_duration.json").write_text(json.dumps(report, indent=1))
    lines = [f"Spoken replies against the {report['limit_s']:g}-second limit ({report['voice']}): {report['within_limit_pct']}% within the limit, longest "
             f"{report['longest_s']} s, estimate off by {report['mean_abs_estimate_error_pct']}% on average, synthesis real-time factor {report['real_time_factor']}.", "",
             "| Reply | estimated s | audio s | speed | sentences spoken | left out | cut | first sentence ready s |", "|---|---:|---:|---:|---:|---:|---|---:|"]
    lines += [f"| {r['reply']} | {r['estimated_s']} | {r['audio_s']} | {r['speed']:g} | {r['sentences_spoken']} | {r['sentences_left_out']} | {'yes' if r['cut'] else 'no'} | "
              f"{r['first_sentence_ready_s']} |" for r in rows]
    (out_dir / "tts_duration.md").write_text("\n".join(lines) + "\n")
    return report
