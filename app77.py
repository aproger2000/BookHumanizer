"""
Chapter Editor v3.6.9 — поддержка промежуточного языка (EN, ES, FR, IT, DE)
"""
import json
import os
import re
import time
import logging
import random
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.6.9"
MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Выбор промежуточного языка (по умолчанию английский)
INTERMEDIATE_LANG = os.environ.get("INTERMEDIATE_LANG", "en").lower()
VALID_LANGS = {"en", "es", "fr", "it", "de"}
if INTERMEDIATE_LANG not in VALID_LANGS:
    logger.warning(f"Unknown INTERMEDIATE_LANG={INTERMEDIATE_LANG}, falling back to 'en'")
    INTERMEDIATE_LANG = "en"

logger.info(f"Intermediate language: {INTERMEDIATE_LANG.upper()} (chain: RU→{INTERMEDIATE_LANG.upper()}→RU)")


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def translate_with_fallback(text: str, target_lang: str = "en", max_retries: int = 2) -> str:
    if not text or len(text.strip()) < 2:
        return text

    url_google = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_lang,
        "dt": "t",
        "q": text
    }
    for attempt in range(max_retries):
        try:
            resp = requests.get(url_google, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                translated = "".join(item[0] for item in data[0] if item[0])
                if translated:
                    return translated
            logger.warning(f"Google attempt {attempt+1} failed: {resp.status_code}")
            time.sleep(1 + random.random())
        except Exception as e:
            logger.warning(f"Google exception: {e}")
            time.sleep(1 + random.random())

    logger.info(f"Falling back to MyMemory (POST) for {target_lang}")
    url_mymemory = "https://api.mymemory.translated.net/get"
    payload = {
        "q": text,
        "langpair": f"auto|{target_lang}",
        "de": "user@example.com"
    }
    for attempt in range(2):
        try:
            resp = requests.post(url_mymemory, data=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("responseStatus") == 200:
                    translated = data.get("responseData", {}).get("translatedText")
                    if translated:
                        return translated
            logger.warning(f"MyMemory attempt {attempt+1} failed: {resp.status_code}")
            time.sleep(1 + random.random())
        except Exception as e:
            logger.warning(f"MyMemory exception: {e}")
            time.sleep(1 + random.random())

    return text


def process_chunk_through_chain(text: str) -> str:
    """Цепочка: RU → INTERMEDIATE_LANG → RU."""
    if not text or len(text.strip()) < 2:
        return text
    try:
        intermediate = translate_with_fallback(text, target_lang=INTERMEDIATE_LANG)
        ru = translate_with_fallback(intermediate, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Chunk processing error: {e}")
        return text


def split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    current_chunk = ""
    paragraphs = text.split('\n\n')
    if len(paragraphs) > 1:
        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= chunk_size:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n\n"
        if current_chunk:
            chunks.append(current_chunk.strip())
        return chunks

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


def apply_translation_chain_full(text: str) -> str:
    logger.info(f"Starting translation chain (RU→{INTERMEDIATE_LANG.upper()}→RU) for {len(text)} chars...")
    chunks = split_text_into_chunks(text)
    logger.info(f"Split into {len(chunks)} chunks")
    processed_chunks = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i+1}/{len(chunks)}...")
        processed = process_chunk_through_chain(chunk)
        processed_chunks.append(processed)

    if '\n\n' in text:
        result = "\n\n".join(processed_chunks)
    else:
        result = " ".join(processed_chunks)
    logger.info(f"Translation complete. Result length: {len(result)}")
    return result


def get_artifact_patterns(lang: str) -> list:
    """Возвращает список паттернов артефактов для конкретного языка."""
    base = [
        r'\bMIT\b',
        r'\bI thought so\b',
        r'\bThey will all call\b',
        r'\bWhat I propose is simple\b',
        r'\bNo bureaucracy\b',
        r'\bNo grant fees\b',
        r'\bIn return nothing\b',
        r'\bIt\'s just that\b',
        r'\bfrom the beginning\b',
        r'\bAlexey remained silent\b',
        r'\bCross continued\b',
        r'\bfunds\?',
        r'\bfunds\.',
        r'\band every day\b',
        r'\bthe offers will become\b',
        r'\bless and less\b',
        r'\bpolite\b',
        r'\byou continue to work\b',
        r'\bwe provide you with peace of mind\b',
        r'\bwhen the world changes\b',
        r'\bwe\'d like you to remember\b',
        r'\bwho your friends were\b',
        r'\bThe sun reflected in their tinted glass\b',
        r'\bAlexey looked down at the black SUVs\b',
        r'\blike a глаза акулы\b',
        r'\bglass like a\b',
        r'\bthe genius\b',
        r'\bof a student\b',
        r'\bfrom Siberia\b',
        r'\bnow he watched\b',
        r'\bas that spark\b',
        r'\bignited its owner\'s career\b',
        r'\bI came to warn you\b',
        r'\bthey want to seduce you\b',
        r'\bthey provide the lab\b',
        r'\bbudget and team\b',
        r'\bwhatever you want\b',
        r'\bhowever research requires a license\b',
        r'\bA group came from\b',
        r'\bsaid more quietly\b',
    ]

    # Язык-специфичные артефакты
    lang_patterns = {
        'es': [
            r'\bque\b', r'\bde\b', r'\bel\b', r'\bla\b', r'\blos\b', r'\blas\b',
            r'\bun\b', r'\buna\b', r'\by\b', r'\bo\b', r'\bpero\b', r'\bes\b',
            r'\bson\b', r'\bestá\b', r'\bestán\b', r'\bcomo\b', r'\bcuando\b',
        ],
        'fr': [
            r'\ble\b', r'\bla\b', r'\bles\b', r'\bde\b', r'\bdes\b', r'\bet\b',
            r'\bou\b', r'\bmais\b', r'\best\b', r'\bsont\b', r'\best-ce\b',
            r'\bque\b', r'\bqui\b', r'\bou\b', r'\bdans\b', r'\bpour\b',
        ],
        'it': [
            r'\bil\b', r'\bla\b', r'\ble\b', r'\bdi\b', r'\bche\b', r'\be\b',
            r'\bo\b', r'\bma\b', r'\bè\b', r'\bsono\b', r'\bper\b', r'\bcon\b',
        ],
        'de': [
            r'\bder\b', r'\bdie\b', r'\bdas\b', r'\bden\b', r'\bdem\b',
            r'\bdes\b', r'\bmit\b', r'\bfür\b', r'\bauf\b', r'\bvon\b',
            r'\bzu\b', r'\bund\b', r'\boder\b', r'\baber\b', r'\bdoch\b',
            r'\bist\b', r'\bwar\b', r'\bhat\b', r'\bsagte\b', r'\bsagten\b',
        ],
    }

    extra = lang_patterns.get(lang, [])
    return base + extra


def minimal_clean(text: str) -> str:
    """
    Удаляем артефакты для выбранного языка.
    """
    patterns = get_artifact_patterns(INTERMEDIATE_LANG)

    for pat in patterns:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)

    # Чистка пробелов
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'^[.,!?;:\s]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'—\s*', '— ', text)
    text = text.replace('""', '"').replace('""', '"')

    # Исправления ошибок
    fixes = [
        (r'черного вина', 'черного кофе'),
        (r'\bТоки\b', '«Ибис»'),
        (r'не испортил', 'не шутил'),
        (r'—\s*знать', '— Я знаю'),
        (r'Босимом', 'Босиком'),
    ]
    for pat, repl in fixes:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)

    return text.strip()


def apply_light_polish(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(' - ', ' — ')
    text = re.sub(r'—\s*', '— ', text)
    return text


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION, chain=INTERMEDIATE_LANG.upper())


@app.post("/api/revise")
def api_revise():
    logger.info(f"=== api_revise: START (lang={INTERMEDIATE_LANG.upper()}) ===")
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

        original_len = len(chapter_text)
        logger.info(f"Original text length: {original_len}")

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": original_len, "percent": 0, "log": f"Начинаем обработку (RU→{INTERMEDIATE_LANG.upper()}→RU)..."})

                # 1. Цепочка переводов
                logger.info(f"Step 1: Translation chain (RU→{INTERMEDIATE_LANG.upper()}→RU)...")
                processed_text = apply_translation_chain_full(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 50, "log": f"Переводы завершены ({len(processed_text)} симв.)"})

                # 2. Минимальная очистка
                logger.info("Step 2: Minimal cleanup...")
                processed_text = minimal_clean(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 70, "log": f"Очистка выполнена ({len(processed_text)} симв.)"})

                # 3. Полировка
                logger.info("Step 3: Light polish...")
                processed_text = apply_light_polish(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 85, "log": f"Полировка выполнена ({len(processed_text)} симв.)"})

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 100, "log": "Готово!"})

                final_len = len(processed_text)
                loss = (original_len - final_len) / original_len
                logger.info(f"Final: {final_len} chars, loss {loss:.2%}")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через цепочку RU→{INTERMEDIATE_LANG.upper()}→RU с минимальной очисткой. Потеря: {loss:.1%}.",
                    "changes": [
                        f"Переведён через Google Translate / MyMemory (RU→{INTERMEDIATE_LANG.upper()}→RU)",
                        "Удалены артефакты перевода"
                    ],
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
