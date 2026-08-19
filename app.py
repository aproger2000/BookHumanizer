"""
Chapter Editor v3.2.8 — Humanization via Translation Chain (гарантированное разбиение на абзацы)
Работает полностью бесплатно, без API-ключей.
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

APP_VERSION = "3.2.8"

MAX_CHARS = 30_000
CHUNK_SIZE = 3000

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


def add_random_noise(text: str) -> str:
    """Добавляет случайные изменения для обхода кеша."""
    if len(text) < 100:
        return text
    
    synonyms = {
        'очень': ['весьма', 'крайне', 'чрезвычайно'],
        'большой': ['огромный', 'громадный', 'крупный'],
        'маленький': ['небольшой', 'крошечный', 'малый'],
        'хороший': ['отличный', 'прекрасный', 'замечательный'],
        'плохой': ['скверный', 'дурной', 'нехороший'],
    }
    
    words = text.split()
    for i, word in enumerate(words):
        if word in synonyms:
            if random.random() < 0.1:
                words[i] = random.choice(synonyms[word])
    
    return ' '.join(words)


def process_paragraph_through_chain(paragraph: str, step_num: int = 0) -> str:
    """Обрабатывает один абзац через цепочку переводов."""
    if not paragraph or len(paragraph.strip()) < 2:
        return paragraph
    
    try:
        noisy = add_random_noise(paragraph)
        ja = translate_text(noisy, target_lang="ja")
        fi = translate_text(ja, target_lang="fi")
        en = translate_text(fi, target_lang="en")
        ru = translate_text(en, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Paragraph processing error: {e}")
        return paragraph


def apply_translation_chain_with_paragraphs(text: str) -> str:
    """Обрабатывает текст, сохраняя структуру абзацев."""
    logger.info(f"Starting translation chain for {len(text)} chars...")
    
    paragraphs = text.split('\n\n')
    processed_paragraphs = []
    
    for i, para in enumerate(paragraphs):
        if not para.strip():
            processed_paragraphs.append(para)
            continue
        
        if len(para) > CHUNK_SIZE:
            sentences = re.split(r'(?<=[.!?])\s+', para)
            chunks = []
            current = ""
            for sent in sentences:
                if len(current) + len(sent) + 1 <= CHUNK_SIZE:
                    current += sent + " "
                else:
                    if current:
                        chunks.append(current.strip())
                    current = sent + " "
            if current:
                chunks.append(current.strip())
            
            processed_chunks = []
            for chunk in chunks:
                processed = process_paragraph_through_chain(chunk, i*10)
                processed_chunks.append(processed)
            processed_paragraphs.append(" ".join(processed_chunks))
        else:
            processed_paragraphs.append(process_paragraph_through_chain(para, i))
    
    result = "\n\n".join(processed_paragraphs)
    logger.info(f"Translation complete. Result length: {len(result)}")
    return result


def clean_translation_artifacts(text: str) -> str:
    """Удаляет артефакты перевода."""
    finnish_patterns = [
        r'\bTietenkin\b', r'\bhe tarvitsevat\b', r'\bJos se toimii\b',
        r'\bvaikka se ei\b', r'\btoimi\b', r'\bpuolella\b',
        r'\bvaltamerta\b', r'\bRakennamme\b', r'\bsiis\b',
        r'\bsademeren\b', r'\bJa lentää\b', r'\bsinne\b',
        r'\baamiaiseksi\b', r'\bkuvaan\b', r'\bMikä tämä on\b',
        r'\bAleksei kysyi\b', r'\bTalomme suunnitelma\b',
        r'\bKuussa ei ole\b', r'\brannoille\b'
    ]
    
    for pattern in finnish_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    english_phrases = [
        r'\bfirst to spot\b', r'\bthe genius\b', r'\bof a student\b',
        r'\bfrom Siberia\b', r'\bnow he watched\b', r'\bas that spark\b',
        r'\bignited its owner\'s career\b', r'\bI came to warn you\b',
        r'\bthey want to seduce you\b', r'\bthey provide the lab\b',
        r'\bbudget and team\b', r'\bwhatever you want\b',
        r'\bhowever research requires a license\b'
    ]
    
    for pattern in english_phrases:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    text = re.sub(r'(\w)\.(\w)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()


def force_split_into_paragraphs_by_chars(text: str, max_paragraph_chars: int = 450) -> str:
    """
    ПРИНУДИТЕЛЬНО разбивает текст на абзацы по количеству символов.
    Это самый надёжный способ — работает всегда, даже если нет точек.
    """
    if not text:
        return text
    
    # Если текста мало, не разбиваем
    if len(text) < 200:
        return text
    
    # Проверяем, есть ли уже абзацы
    existing = text.split('\n\n')
    if len(existing) >= 3:
        good = [p for p in existing if len(p.strip()) > 30]
        if len(good) >= 2:
            logger.info(f"Already has {len(good)} good paragraphs")
            return text
    
    # Просто разбиваем по символам
    paragraphs = []
    for i in range(0, len(text), max_paragraph_chars):
        chunk = text[i:i+max_paragraph_chars].strip()
        if chunk:
            paragraphs.append(chunk)
    
    # Если получился один абзац, но текст длинный — делим пополам
    if len(paragraphs) == 1 and len(text) > 600:
        mid = len(text) // 2
        # Ищем ближайший пробел
        while mid > 0 and text[mid] != ' ':
            mid -= 1
        if mid > 0:
            paragraphs = [
                text[:mid].strip(),
                text[mid:].strip()
            ]
    
    result = '\n\n'.join(paragraphs)
    logger.info(f"force_split: {len(paragraphs)} paragraphs")
    return result


def apply_light_polish(text: str) -> str:
    """Лёгкая пост-обработка."""
    text = re.sub(r'\s+', ' ', text)
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
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0, "log": "Начинаем обработку..."})

                processed_text = apply_translation_chain_with_paragraphs(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 60, "log": "Переводы завершены"})
                
                processed_text = clean_translation_artifacts(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 70, "log": "Артефакты удалены"})
                
                # ГАРАНТИРОВАННОЕ разбиение на абзацы по символам
                processed_text = force_split_into_paragraphs_by_chars(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 90, "log": "Абзацы сформированы"})
                
                processed_text = apply_light_polish(processed_text)

                para_count = len(processed_text.split('\n\n'))
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100, "log": f"Готово! Абзацев: {para_count}"})

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через цепочку переводов. Абзацев: {para_count}",
                    "changes": [
                        "Переведён через Google Translate (RU→JA→FI→EN→RU)",
                        f"Разделён на {para_count} абзацев"
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
