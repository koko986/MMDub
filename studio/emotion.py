from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from .text import normalize_text, split_sentences, syllable_count


STYLES = ("neutral", "warm", "happy", "sad", "angry", "excited", "serious", "inquisitive")
DELIVERY_MODES = ("consistent", "conversational", "dramatic")

# A neutral reference cannot create a truly acted performance, but these restrained
# timing/loudness recipes make automatic delivery audible until a matching style
# recording is supplied. Values are deliberately subtle to preserve speaker identity.
STYLE_PROSODY = {
    "neutral": (1.00, 1.00, 1.00),
    "warm": (0.96, 1.02, 1.12),
    "happy": (1.05, 1.04, 0.90),
    "sad": (0.90, 0.94, 1.25),
    "angry": (1.04, 1.08, 0.80),
    "excited": (1.10, 1.08, 0.72),
    "serious": (0.94, 1.01, 1.18),
    "inquisitive": (1.00, 1.02, 1.08),
}


def prosody_for(style: str, intensity: float, has_style_recording: bool) -> dict:
    """Return stable synthesis/post-processing settings for one sentence."""
    speed_factor, gain, pause_factor = STYLE_PROSODY.get(style, STYLE_PROSODY["neutral"])
    strength = max(0.0, min(1.0, float(intensity)))
    # Recorded acting already carries most of the expression, so do less DSP.
    if has_style_recording:
        strength *= 0.35
    return {
        "speed_factor": 1.0 + (speed_factor - 1.0) * strength,
        "gain": 1.0 + (gain - 1.0) * strength,
        "pause_factor": 1.0 + (pause_factor - 1.0) * strength,
    }

HAPPY = ("ပျော်", "ပျော်ရွှင်", "ဝမ်းသာ", "ပီတိ", "ကောင်း", "အောင်မြင်", "ရယ်")
SAD = ("ဝမ်းနည်း", "ငို", "ဆုံးရှုံး", "နာကျင်", "လွမ်း", "စိတ်မကောင်း", "သေဆုံး")
ANGRY = ("ဒေါသ", "မုန်း", "မကျေနပ်", "တော်ပြီ", "ရပ်", "မလုပ်", "အရမ်းဆိုး")
WARM = ("ကျေးဇူး", "ချစ်", "ကြိုဆို", "ဂရုစိုက်", "မင်္ဂလာ", "ကူညီ")
SERIOUS = ("အရေးကြီး", "သတိ", "လိုအပ်", "စည်းကမ်း", "တာဝန်", "ဆုံးဖြတ်")
INTENSE = ("အရမ်း", "အလွန်", "တကယ်", "လုံးဝ", "အံ့ဩ")


@dataclass
class EmotionResult:
    style: str
    confidence: float
    intensity: float
    explanation: str


class EmotionAnalyzer:
    """Local CPU sentiment plus conservative Burmese intent rules."""

    def __init__(self) -> None:
        self._classifier = None
        self._attempted = False
        self._lock = threading.Lock()
        self.state = "waiting"
        self.message = "Emotion analyzer loads when needed."

    def status(self) -> dict:
        return {"state": self.state, "message": self.message, "local_only": True}

    def preload(self) -> None:
        """Start the optional CPU model download without blocking rule analysis."""
        self._load()

    @staticmethod
    def _sylbreak(line: str) -> str:
        pattern = re.compile(
            r"((?<!္)[က-အ](?![်္])|[a-zA-Z0-9ဣဤဥဦဧဩဪဿ၌၍၏၀-၉၊။!?.,:;@\s])"
        )
        return pattern.sub(r" \1", line).strip()

    def _load(self):
        if self._classifier is not None:
            return self._classifier
        if self._attempted:
            return None
        with self._lock:
            if self._classifier is not None:
                return self._classifier
            if self._attempted:
                return None
            self._attempted = True
            self.state = "loading"
            self.message = "Loading local Burmese sentiment analyzer on CPU…"
            threading.Thread(
                target=self._load_in_background,
                daemon=True,
                name="emotion-analyzer-loader",
            ).start()
            threading.Timer(20.0, self._mark_basic_while_downloading).start()
        return None

    def _mark_basic_while_downloading(self) -> None:
        with self._lock:
            if self.state == "loading" and self._classifier is None:
                self.state = "basic"
                self.message = (
                    "Basic local analysis is ready while the enhanced analyzer downloads "
                    "in the background."
                )

    def _load_in_background(self) -> None:
        try:
            from transformers import pipeline

            classifier = pipeline(
                "text-classification",
                model="thutasann/mm_sentiment_model_v1",
                device=-1,
            )
            with self._lock:
                self._classifier = classifier
                self.state = "ready"
                self.message = "Local Burmese emotion analysis ready."
        except Exception as exc:
            with self._lock:
                self.state = "basic"
                self.message = f"Basic rule analysis active: {exc}"

    @staticmethod
    def _contains(text: str, values: tuple[str, ...]) -> bool:
        compact = text.replace(" ", "")
        return any(value.replace(" ", "") in compact for value in values)

    def analyze_sentence(self, text: str, delivery_mode: str = "consistent") -> EmotionResult:
        text = normalize_text(text)
        scores = {style: 0.0 for style in STYLES}
        reasons: list[str] = []
        if "?" in text or text.endswith("လား။") or text.endswith("မလား။"):
            scores["inquisitive"] += 0.9
            reasons.append("question")
        if self._contains(text, HAPPY):
            scores["happy"] += 0.85
            reasons.append("positive words")
        if self._contains(text, SAD):
            scores["sad"] += 1.0
            reasons.append("sad words")
        if self._contains(text, ANGRY):
            scores["angry"] += 1.0
            reasons.append("anger words")
        if self._contains(text, WARM):
            scores["warm"] += 0.75
            reasons.append("warm words")
        if self._contains(text, SERIOUS):
            scores["serious"] += 0.8
            reasons.append("serious words")
        intense = self._contains(text, INTENSE) or "!" in text
        if intense:
            scores["excited"] += 0.35
            reasons.append("strong emphasis")

        classifier = self._load()
        if classifier is not None:
            try:
                result = classifier(self._sylbreak(text), truncation=True, max_length=256)[0]
                label = result.get("label", "LABEL_2")
                confidence = float(result.get("score", 0.0))
                index = int(label.rsplit("_", 1)[-1]) if "_" in label else 2
                if index >= 4:
                    scores["excited" if intense else "happy"] += confidence * 0.85
                elif index == 3:
                    scores["warm"] += confidence * 0.65
                elif index == 1:
                    scores["sad"] += confidence * 0.65
                elif index <= 0:
                    target = "angry" if self._contains(text, ANGRY) else "sad"
                    scores[target] += confidence * 0.85
                reasons.append("local sentiment model")
            except Exception:
                pass

        style, score = max(scores.items(), key=lambda item: item[1])
        thresholds = {"consistent": 0.72, "conversational": 0.52, "dramatic": 0.35}
        if score < thresholds.get(delivery_mode, 0.72):
            style = "neutral"
        confidence = min(0.99, max(0.35 if style != "neutral" else 0.5, score))
        intensity = 0.35 if delivery_mode == "consistent" else 0.6 if delivery_mode == "conversational" else 0.85
        if intense:
            intensity = min(1.0, intensity + 0.1)
        return EmotionResult(style, round(confidence, 3), round(intensity, 2), ", ".join(reasons) or "neutral fallback")

    def analyze_text(self, text: str, delivery_mode: str = "consistent") -> list[dict]:
        if delivery_mode not in DELIVERY_MODES:
            delivery_mode = "consistent"
        results: list[dict] = []
        position = 0
        paragraphs = [part.strip() for part in re.split(r"\n+", normalize_text(text)) if part.strip()]
        last_style = "neutral"
        for paragraph_index, paragraph in enumerate(paragraphs):
            sentence_results = []
            for sentence in split_sentences(paragraph):
                result = self.analyze_sentence(sentence, delivery_mode)
                sentence_results.append((sentence, result))
            if not sentence_results:
                continue
            winner = max(sentence_results, key=lambda item: item[1].confidence)[1]
            paragraph_style = winner.style
            if delivery_mode == "consistent" and winner.confidence < 0.82 and last_style != "neutral":
                paragraph_style = last_style
            for sentence, result in sentence_results:
                effective = paragraph_style if delivery_mode == "consistent" else result.style
                results.append(
                    {
                        "position": position,
                        "paragraph_index": paragraph_index,
                        "text": sentence,
                        "syllables": syllable_count(sentence),
                        "detected_style": result.style,
                        "effective_style": effective,
                        "confidence": result.confidence,
                        "intensity": result.intensity,
                        "explanation": result.explanation,
                        "manual_override": False,
                    }
                )
                position += 1
            last_style = paragraph_style
        return results
