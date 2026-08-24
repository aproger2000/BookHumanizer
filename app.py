"""
Chapter Editor v4.0.0 — локальное перефразирование с ruT5-small (без внешних API)
"""
import json
import os
import re
import time
import logging
import random
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

# Импорт transformers (установить отдельно)
try:
    from transformers import T5ForConditionalGeneration, T5Tokenizer
    import torch
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.0.0"
MAX_CHARS = 30_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Глобальные переменные для модели
_model = None
_tokenizer = None
_MODEL_LOADED = False


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def load_model():
    """Ленивая загрузка модели ruT5-small."""
    global _model, _tokenizer, _MODEL_LOADED
    if _MODEL_LOADED:
        return

    if not TRANSFORMERS_AVAILABLE:
        logger.error("transformers not installed. Please install: transformers torch sentencepiece")
        return

    try:
        model_name = "cointegrated/ruT5-small"
        logger.info(f"Loading model: {model_name}...")
        _tokenizer = T5Tokenizer.from_pretrained(model_name)
        _model = T5ForConditionalGeneration.from_pretrained(model_name)
        _model.eval()
        _MODEL_LOADED = True
        logger.info("Model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        _MODEL_LOADED = False


def paraphrase_with_model(text: str) -> str:
    """Перефразирует текст с помощью ruT5-small."""
    if not text or len(text) < 10:
        return text

    if not _MODEL_LOADED:
        load_model()
        if not _MODEL_LOADED:
            return text

    try:
        # Формируем промпт для T5
        input_text = f"paraphrase: {text}"
        inputs = _tokenizer(input_text, return_tensors="pt", truncation=True, max_length=256)

        with torch.no_grad():
            outputs = _model.generate(
                **inputs,
                max_length=256,
                temperature=0.7,
                do_sample=True,
                top_p=0.9,
                repetition_penalty=1.1,
                num_beams=1
            )
        paraphrased = _tokenizer.decode(outputs[0], skip_special_tokens=True)

        # Если перефразирование не удалось, возвращаем исходный текст
        if paraphrased and len(paraphrased) > 5:
            return paraphrased
        return text
    except Exception as e:
        logger.warning(f"Paraphrasing error: {e}")
        return text


def split_paragraphs(text: str) -> list:
    if not text:
        return []
    text = text.replace('\r\n', '\n')
    paragraphs = text.split('\n\n')
    return [p.strip() for p in paragraphs if p.strip()]


def get_human_score(text: str) -> int:
    """Встроенный детектор HUMAN (для обратной связи)."""
    if not text or len(text) < 20:
        return 50

    letters = sum(1 for ch in text if ch.isalpha())
    if letters == 0:
        return 80
    latin_count = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
    latin_ratio = latin_count / letters
    score = max(0, 100 - (latin_ratio * 120))

    markers = [
        r'\bI thought so\b', r'\bNo bureaucracy\b', r'\bNo grant fees\b',
        r'\bIn return nothing\b', r'\bfrom the beginning\b',
        r'\bAlexey remained silent\b', r'\bCross continued\b',
        r'\bfunds\?', r'\bthe offers will become\b', r'\bless and less\b',
        r'\bpolite\b', r'\byou continue to work\b',
        r'\bwe provide you with peace of mind\b', r'\bwhen the world changes\b',
        r'\bwe\'d like you to remember\b', r'\bwho your friends were\b',
        r'Vino quieren alejarte', r'Laboratorio, presupuesto',
        r'Empty Null: Final Drawings', r'««««Ибис»»»»',
    ]
    marker_penalty = 0
    for m in markers:
        if re.search(m, text, flags=re.IGNORECASE):
            marker_penalty += 15
    score -= marker_penalty

    words = re.findall(r'[а-яА-Яa-zA-Z]+', text)
    if words:
        avg_len = sum(len(w) for w in words) / len(words)
        if avg_len < 3 or avg_len > 12:
            score -= 10

    word_counts = {}
    for w in words:
        w_lower = w.lower()
        word_counts[w_lower] = word_counts.get(w_lower, 0) + 1
    max_repeat = max(word_counts.values()) if word_counts else 0
    if max_repeat > len(words) * 0.15:
        score -= 15

    return max(0, min(100, int(score)))


def process_paragraph(paragraph: str) -> dict:
    """Обрабатывает один абзац локальной моделью."""
    if not paragraph:
        return {"original": paragraph, "revised": paragraph, "status": "error", "chain": "LOCAL", "human_score": 0}

    # Применяем перефразирование
    revised = paraphrase_with_model(paragraph)

    # Если модель не изменила текст, применяем минимальные замены (резерв)
    if revised == paragraph:
        # Простые синонимы для диалоговых тегов
        replacements = {
            r'\bсказал\b': random.choice(['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал']),
            r'\bсказала\b': random.choice(['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала']),
            r'\bспросил\b': random.choice(['поинтересовался', 'осведомился', 'полюбопытствовал']),
            r'\bспросила\b': random.choice(['поинтересовалась', 'осведомилась', 'полюбопытствовала']),
        }
        for pattern, replacement in replacements.items():
            revised = re.sub(pattern, replacement, revised, flags=re.IGNORECASE)

    score = get_human_score(revised)

    return {
        "original": paragraph,
        "revised": revised,
        "status": "done" if score > 50 else "partial",
        "chain": "LOCAL",
        "human_score": score
    }


def analyze_overall(text: str) -> dict:
    if not text or len(text) < 100:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    segments = re.split(r'(?<=[.!?])\s+', text)
    if len(segments) < 3:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    results = {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0}
    for seg in segments:
        if len(seg) < 15:
            continue
        score = get_human_score(seg)
        if score < 30:
            results["AI"] += 1
        elif score < 50:
            results["LIKELY_AI"] += 1
        elif score < 70:
            results["LIKELY_HUMAN"] += 1
        else:
            results["HUMAN"] += 1

    total = sum(results.values())
    if total == 0:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    for k in results:
        results[k] = int(results[k] / total * 100)

    score = results["HUMAN"] * 1.0 + results["LIKELY_HUMAN"] * 0.7 + results["LIKELY_AI"] * 0.3
    results["score"] = int(score)
    return results


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    logger.info(f"=== api_revise: START (v{APP_VERSION}) ===")
    try:
        file_storage = request.files.get("file")
        text = request.form.get("text", "")
        style = request.form.get("style", "neutral")

        if file_storage and file_storage.filename:
            raw = file_storage.read()
            chapter_text = raw.decode("utf-8", errors="replace")
        elif text.strip():
            chapter_text = text
        else:
            return jsonify(detail="Provide chapter text or upload a file."), 400

        chapter_text = chapter_text.strip()
        if not chapter_text:
            return jsonify(detail="Chapter text is empty."), 400

        if len(chapter_text) > MAX_CHARS:
            chapter_text = chapter_text[:MAX_CHARS]
            logger.warning(f"Truncated text to {MAX_CHARS} chars")

        paragraphs = split_paragraphs(chapter_text)
        total = len(paragraphs)
        logger.info(f"Split into {total} paragraphs")

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем локальную обработку {total} абзацев..."})

                results = []
                for idx, para in enumerate(paragraphs):
                    yield _sse("paragraph_start", {
                        "index": idx,
                        "original": para,
                        "status": "processing"
                    })

                    try:
                        result = process_paragraph(para)
                    except Exception as e:
                        logger.error(f"Paragraph {idx} processing error: {e}")
                        result = {
                            "original": para,
                            "revised": para,
                            "status": "error",
                            "chain": "LOCAL",
                            "human_score": 0
                        }
                    results.append(result)

                    yield _sse("paragraph_status", {
                        "index": idx,
                        "original": result["original"],
                        "revised": result["revised"],
                        "status": result["status"],
                        "chain": result["chain"],
                        "human_score": result.get("human_score", 0)
                    })

                    yield _sse("progress", {
                        "chars": idx + 1,
                        "estimated_total": total,
                        "percent": (idx + 1) / total * 100,
                        "log": f"Обработано {idx+1}/{total} абзацев"
                    })

                    yield _sse("paragraph_progress", {
                        "current": idx + 1,
                        "total": total,
                        "percent": (idx + 1) / total * 100
                    })

                final_text = "\n\n".join(r["revised"] for r in results)

                scores = [r.get("human_score", 0) for r in results if r.get("human_score", 0) > 0]
                avg_score = sum(scores) // len(scores) if scores else 0

                overall = analyze_overall(final_text)

                status_counts = {"done": 0, "partial": 0, "error": 0}
                for r in results:
                    status_counts[r.get("status", "error")] += 1

                logger.info(f"Статусы абзацев: {status_counts}")

                yield _sse("done", {
                    "revised_text": final_text,
                    "original_text": chapter_text,
                    "summary": f"Обработано {total} абзацев локально (ruT5). Успешно: {status_counts['done']}, частично: {status_counts['partial']}, ошибок: {status_counts['error']}. Средний HUMAN: {avg_score}%",
                    "paragraphs": results,
                    "average_human_score": avg_score,
                    "overall_analysis": overall,
                    "status_counts": status_counts,
                    "checklist": []
                })
            except ChapterEditError as e:
                logger.exception("ChapterEditError")
                yield _sse("error", {"detail": str(e)})
            except Exception as e:
                logger.exception("Unexpected error in generate")
                yield _sse("error", {"detail": f"Unexpected error: {str(e)}"})

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    except Exception as e:
        logger.exception("api_revise: Unexpected error")
        return jsonify(detail=f"Server error: {str(e)}"), 500


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.errorhandler(HTTPException)
def handle_http_exception(exc):
    return jsonify(detail=exc.description or str(exc)), exc.code or 500


@app.errorhandler(Exception)
def handle_exception(exc):
    logger.exception("Unhandled exception")
    return jsonify(detail=f"Server error: {str(exc)}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
