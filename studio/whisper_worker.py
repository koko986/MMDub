from __future__ import annotations

import argparse
import gc
import json
import threading
from pathlib import Path


class PersistentWhisper:
    """One warm faster-whisper model shared by all video jobs."""

    def __init__(self, model_name: str = "small"):
        self.model_name = model_name
        self._lock = threading.Lock()
        self._cuda_model = None
        self._cuda_pipeline = None
        self._cpu_model = None

    def _cuda(self):
        if self._cuda_pipeline is None:
            from faster_whisper import BatchedInferencePipeline, WhisperModel
            self._cuda_model = WhisperModel(self.model_name, device="cuda", compute_type="int8_float16")
            self._cuda_pipeline = BatchedInferencePipeline(model=self._cuda_model)
        return self._cuda_pipeline

    def _cpu(self):
        if self._cpu_model is None:
            from faster_whisper import WhisperModel
            self._cpu_model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._cpu_model

    @staticmethod
    def _result(segments, info, device: str, warning: str = "") -> dict:
        items = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            items.append({
                "start": float(segment.start), "end": float(segment.end), "text": text,
                "confidence": max(0.0, min(1.0, 1.0 + float(getattr(segment, "avg_logprob", -.5)))),
                "avg_logprob": float(getattr(segment, "avg_logprob", 0.0)),
                "words": [
                    {"start": float(word.start or segment.start), "end": float(word.end or segment.end),
                     "word": word.word.strip()}
                    for word in (segment.words or []) if word.word.strip()
                ],
            })
        return {"language": info.language, "language_probability": float(info.language_probability),
                "segments": items, "device": device, "warning": warning}

    def transcribe(self, source: str | Path) -> dict:
        with self._lock:
            cuda_error: Exception | None = None
            for batch_size in (4, 2, 1):
                try:
                    segments, info = self._cuda().transcribe(
                        str(source), language=None, beam_size=1, batch_size=batch_size,
                        vad_filter=True, word_timestamps=True,
                    )
                    result = self._result(list(segments), info, f"CUDA · batch {batch_size}")
                    return self._refine_uncertain(source, result, self._cuda_model)
                except Exception as exc:
                    cuda_error = exc
                    if not any(token in str(exc).lower() for token in ("cuda", "memory", "cublas", "cudnn")):
                        break
            self._cuda_pipeline = None
            self._cuda_model = None
            gc.collect()
            segments, info = self._cpu().transcribe(
                str(source), language=None, beam_size=1, vad_filter=True, word_timestamps=True
            )
            detail = str(cuda_error or "CUDA is unavailable").splitlines()[0][:160]
            result = self._result(list(segments), info, "CPU",
                                  f"GPU transcription was unavailable, so slower CPU mode was used ({detail}).")
            return self._refine_uncertain(source, result, self._cpu_model)

    @staticmethod
    def _refine_uncertain(source: str | Path, result: dict, model) -> dict:
        uncertain = [index for index, item in enumerate(result["segments"])
                     if item.get("avg_logprob", 0) < -1.0 or item.get("confidence", 1) < .2][:8]
        if not uncertain or model is None:
            result["selective_refinements"] = 0
            return result
        try:
            from faster_whisper.audio import decode_audio
            audio = decode_audio(str(source), sampling_rate=16000)
            refined = 0
            for index in uncertain:
                original = result["segments"][index]
                offset = max(0.0, float(original["start"]) - .35)
                end = min(len(audio) / 16000, float(original["end"]) + .35)
                excerpt = audio[round(offset * 16000):round(end * 16000)]
                segments, _ = model.transcribe(
                    excerpt, language=result.get("language") or None, beam_size=5,
                    vad_filter=False, word_timestamps=True,
                )
                candidates = list(segments)
                text = " ".join(item.text.strip() for item in candidates if item.text.strip()).strip()
                score = max((float(getattr(item, "avg_logprob", -99)) for item in candidates), default=-99)
                if text and score > float(original.get("avg_logprob", -99)):
                    original["text"] = text
                    original["avg_logprob"] = score
                    original["confidence"] = max(0.0, min(1.0, 1.0 + score))
                    original["words"] = [
                        {"start": offset + float(word.start or item.start),
                         "end": offset + float(word.end or item.end), "word": word.word.strip()}
                        for item in candidates for word in (item.words or []) if word.word.strip()
                    ]
                    refined += 1
            result["selective_refinements"] = refined
        except Exception:
            result["selective_refinements"] = 0
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()

    service = PersistentWhisper()
    if args.device == "cpu":
        segments, info = service._cpu().transcribe(
            args.input, language=None, beam_size=1, vad_filter=True, word_timestamps=True
        )
        output = service._result(list(segments), info, "CPU")
    else:
        output = service.transcribe(args.input)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
