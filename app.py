"""
Chapter Editor v3.0.2 — Humanization via Translation Chain (только Google Translate)
Работает полностью бесплатно, без API-ключей.
"""
import io
import json
import os
import re
import time
import logging
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.0.2"

MAX_CHARS = 10_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def translate_text(text: str, target_lang: str = "en") -> str:
    """Переводит текст через публичный API Google Translate."""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        translated = ""
        for item in data[0]:
            if item[0]:
                translated += item[0]
        return translated or text
    except Exception as e:
        logger.error(f"Translate error: {e}")
        return text


def apply_translation_chain(text: str) -> str:
    """Цепочка переводов через Google Translate."""
    logger.info("Starting translation chain...")
    
    # Шаг 1: Английский → Японский
    ja_text = translate_text(text, target_lang="ja")
    logger.info(f"EN->JA complete. Length: {len(ja_text)}")
    
    # Шаг 2: Японский → Финский
    fi_text = translate_text(ja_text, target_lang="fi")
    logger.info(f"JA->FI complete. Length: {len(fi_text)}")
    
    # Шаг 3: Финский → Английский
    final_text = translate_text(fi_text, target_lang="en")
    logger.info(f"FI->EN complete. Length: {len(final_text)}")
    
    return final_text


def apply_light_polish(text: str) -> str:
    """Лёгкая пост-обработка для естественности."""
    # Убираем повторяющиеся пробелы
    text = re.sub(r'\s+', ' ', text)
    # Восстанавливаем правильные кавычки и тире
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(' - ', ' — ')
    # Разбиваем на абзацы, если их нет
    if len(text) > 500 and '\n\n' not in text:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if len(sentences) > 5:
            mid = len(sentences) // 2
            text = ' '.join(sentences[:mid]) + '\n\n' + ' '.join(sentences[mid:])
    return text


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


STYLE_PRESETS = {
    "neutral": "",
    "dynamic_scifi": "",
}


@app.post("/api/revise")
def api_revise():
    logger.info("=== api_revise: START ===")
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

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0})

                logger.info("Applying translation chain...")
                processed_text = apply_translation_chain(chapter_text)
                processed_text = apply_light_polish(processed_text)

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100})

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": "Текст переработан через цепочку переводов (EN→JA→FI→EN)",
                    "changes": [
                        "Переведён через Google Translate на японский",
                        "Переведён через Google Translate на финский",
                        "Переведён обратно на английский"
                    ],
                    "checklist": []
                })
            except ChapterEditError as e:
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
