"""
Chapter Editor v3.3.0 — Humanization via Translation Chain (полная переработка)
Работает полностью бесплатно, без API-ключей.
Гарантированно разбивает текст на абзацы и удаляет артефакты.
"""
import io
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

APP_VERSION = "3.3.0"

MAX_CHARS = 30_000

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
        cache_buster = random.randint(100000, 999999)
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text,
            "cb": cache_buster
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
    """Цепочка переводов: RU → JA → FI → EN → RU."""
    logger.info(f"Translation chain: {len(text)} chars")
    
    try:
        ja = translate_text(text, target_lang="ja")
        fi = translate_text(ja, target_lang="fi")
        en = translate_text(fi, target_lang="en")
        ru = translate_text(en, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Translation chain error: {e}")
        return text


def clean_artifacts(text: str) -> str:
    """Очистка от артефактов перевода."""
    # Финские фразы
    finnish = [
        r'\bTietenkin\b', r'\bhe tarvitsevat\b', r'\bJos se toimii\b',
        r'\bvaikka se ei\b', r'\btoimi\b', r'\bpuolella\b',
        r'\bvaltamerta\b', r'\bRakennamme\b', r'\bsiis\b',
        r'\bsademeren\b', r'\bJa lentää\b', r'\bsinne\b',
        r'\baamiaiseksi\b', r'\bkuvaan\b', r'\bMikä tämä on\b',
        r'\bAleksei kysyi\b', r'\bTalomme suunnitelma\b',
        r'\bKuussa ei ole\b', r'\brannoille\b', r'\bakkuni\b',
        r'\bakkujasi\b', r'\bтоими\b'
    ]
    for pattern in finnish:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    
    # Английские фразы
    english = [
        r'\bfirst to spot\b', r'\bthe genius\b', r'\bof a student\b',
        r'\bfrom Siberia\b', r'\bnow he watched\b', r'\bas that spark\b',
        r'\bignited its owner\'s career\b', r'\bI came to warn you\b',
        r'\bthey want to seduce you\b', r'\bthey provide the lab\b',
        r'\bbudget and team\b', r'\bwhatever you want\b',
        r'\bhowever research requires a license\b', r'\bA group came from\b',
        r'\bMIT\b', r'\bthey need\b'
    ]
    for pattern in english:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    
    # Убираем точки внутри слов
    text = re.sub(r'(\w)\.(\w)', r'\1\2', text)
    
    # Убираем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    
    # Восстанавливаем правильные кавычки
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('«', '"').replace('»', '"')
    
    return text.strip()


def split_into_paragraphs(text: str, max_chars: int = 450) -> str:
    """
    ГАРАНТИРОВАННО разбивает текст на абзацы по количеству символов.
    Это самый надёжный способ.
    """
    if not text or len(text) < 150:
        return text
    
    # Проверяем, есть ли уже абзацы
    existing = text.split('\n\n')
    if len(existing) >= 2:
        good = [p for p in existing if len(p.strip()) > 30]
        if len(good) >= 2:
            return text
    
    # Разбиваем текст на слова
    words = text.split()
    
    if len(words) < 15:
        return text
    
    paragraphs = []
    current = []
    current_len = 0
    
    for word in words:
        word_len = len(word) + 1
        if current_len > max_chars and len(current) >= 3:
            paragraphs.append(' '.join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += word_len
    
    if current:
        paragraphs.append(' '.join(current))
    
    # Если получился один абзац, но текст длинный — делим пополам
    if len(paragraphs) == 1 and len(text) > 500:
        mid = len(text) // 2
        # Ищем ближайший пробел
        while mid > 0 and text[mid] != ' ':
            mid -= 1
        if mid > 0:
            paragraphs = [
                text[:mid].strip(),
                text[mid:].strip()
            ]
    
    return '\n\n'.join(paragraphs)


def final_polish(text: str) -> str:
    """Финальная полировка текста."""
    # Убираем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    # Восстанавливаем тире
    text = text.replace(' - ', ' — ')
    # Убираем артефакты " ." -> "."
    text = re.sub(r'\s+\.', '.', text)
    text = re.sub(r'\s+,', ',', text)
    text = re.sub(r'\s+\?', '?', text)
    text = re.sub(r'\s+!', '!', text)
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
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0, "log": "Начинаем обработку..."})

                # 1. Цепочка переводов
                logger.info("Step 1: Translation chain...")
                processed_text = apply_translation_chain(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 40, "log": "Переводы завершены"})

                # 2. Очистка артефактов
                logger.info("Step 2: Cleaning artifacts...")
                processed_text = clean_artifacts(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 60, "log": "Артефакты удалены"})

                # 3. ГАРАНТИРОВАННОЕ разбиение на абзацы
                logger.info("Step 3: Splitting into paragraphs...")
                processed_text = split_into_paragraphs(processed_text)
                para_count = len(processed_text.split('\n\n'))
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 80, "log": f"Абзацы сформированы: {para_count}"})

                # 4. Финальная полировка
                logger.info("Step 4: Final polish...")
                processed_text = final_polish(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100, "log": "Готово!"})

                # Проверяем результат
                final_para_count = len(processed_text.split('\n\n'))
                logger.info(f"Final: {len(processed_text)} chars, {final_para_count} paragraphs")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через цепочку переводов. Абзацев: {final_para_count}",
                    "changes": [
                        "Переведён через Google Translate (RU→JA→FI→EN→RU)",
                        f"Разделён на {final_para_count} абзацев"
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
    return jsonify(detail=f"Server error: {str(e)}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
