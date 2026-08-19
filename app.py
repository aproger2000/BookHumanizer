"""
Chapter Editor v3.0.4 — Humanization via Translation Chain (с разбивкой на части)
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

APP_VERSION = "3.0.4"

MAX_CHARS = 30_000  # Увеличили для больших текстов
CHUNK_SIZE = 3000   # Размер части для перевода

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def translate_text(text: str, target_lang: str = "en") -> str:
    """Переводит текст через публичный API Google Translate."""
    if not text or len(text.strip()) < 2:
        return text
    
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


def split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    """Разбивает текст на части по предложениям."""
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    current_chunk = ""
    
    # Разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= chunk_size:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def process_chunk_through_chain(text: str) -> str:
    """Обрабатывает один фрагмент через цепочку переводов."""
    if not text or len(text.strip()) < 2:
        return text
    
    try:
        # RU -> JA
        ja = translate_text(text, target_lang="ja")
        # JA -> FI
        fi = translate_text(ja, target_lang="fi")
        # FI -> EN
        en = translate_text(fi, target_lang="en")
        # EN -> RU
        ru = translate_text(en, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Chunk processing error: {e}")
        return text


def apply_translation_chain_full(text: str) -> str:
    """Обрабатывает весь текст, разбивая на части."""
    logger.info(f"Starting translation chain for {len(text)} chars...")
    
    chunks = split_text_into_chunks(text)
    logger.info(f"Split into {len(chunks)} chunks")
    
    processed_chunks = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i+1}/{len(chunks)}...")
        processed = process_chunk_through_chain(chunk)
        processed_chunks.append(processed)
    
    result = " ".join(processed_chunks)
    logger.info(f"Translation complete. Result length: {len(result)}")
    return result


def apply_light_polish(text: str) -> str:
    """Лёгкая пост-обработка для естественности."""
    # Убираем повторяющиеся пробелы
    text = re.sub(r'\s+', ' ', text)
    # Восстанавливаем правильные кавычки и тире
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(' - ', ' — ')
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
                processed_text = apply_translation_chain_full(chapter_text)
                processed_text = apply_light_polish(processed_text)

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100})

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": "Текст переработан через цепочку переводов (RU→JA→FI→EN→RU)",
                    "changes": [
                        "Переведён через Google Translate на японский",
                        "Переведён через Google Translate на финский",
                        "Переведён на английский",
                        "Переведён обратно на русский"
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
