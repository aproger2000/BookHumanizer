"""
Chapter Editor v3.5.0 — Humanization via Translation Chain + выделение диалогов в абзацы
Работает с Google Translate.
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

APP_VERSION = "3.5.0"

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
    """Разбивает текст на части, сохраняя абзацы."""
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


def process_chunk_through_chain(text: str) -> str:
    """Обрабатывает один фрагмент через цепочку переводов."""
    if not text or len(text.strip()) < 2:
        return text
    
    try:
        ja = translate_text(text, target_lang="ja")
        fi = translate_text(ja, target_lang="fi")
        en = translate_text(fi, target_lang="en")
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
    
    result = "\n\n".join(processed_chunks)
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


def split_into_paragraphs_by_logic(text: str) -> str:
    """
    Разбивает текст на абзацы по логике изложения.
    Каждый диалог — отдельный абзац.
    """
    if not text or len(text) < 200:
        return text
    
    # Если уже есть хорошие абзацы — не трогаем
    existing = text.split('\n\n')
    if len(existing) >= 4:
        good = [p for p in existing if len(p.strip()) > 30]
        if len(good) >= 3:
            return text
    
    # Разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    if len(sentences) < 5:
        return force_split_by_length(text)
    
    paragraphs = []
    current_para = []
    current_len = 0
    is_in_dialog = False
    
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        
        # Проверяем, является ли предложение диалогом
        is_dialog = (
            sent.startswith('—') or 
            sent.startswith('"') or 
            sent.startswith('«') or
            sent.startswith('–')
        )
        
        # Проверяем, не начинается ли предложение с новой темы
        is_new_topic = any(sent.startswith(w) for w in ['Алексей', 'Масарик', 'Анна', 'Кросс', 'Он', 'Она', 'Они'])
        
        # Проверяем длину
        is_long = len(sent) > 200
        
        # === НОВАЯ ЛОГИКА ДЛЯ ДИАЛОГОВ ===
        if is_dialog:
            # Если это диалог, а текущий абзац не пустой — закрываем его
            if current_para:
                paragraphs.append(' '.join(current_para))
                current_para = []
                current_len = 0
            # Добавляем диалог как отдельный абзац
            paragraphs.append(sent)
            is_in_dialog = True
            continue
        
        # Если вышли из диалога
        if is_in_dialog and not is_dialog:
            is_in_dialog = False
            # Если есть накопленный текст — начинаем новый абзац
            if current_para:
                paragraphs.append(' '.join(current_para))
                current_para = []
                current_len = 0
        
        # === ЛОГИКА ДЛЯ ОБЫЧНЫХ ПРЕДЛОЖЕНИЙ ===
        should_break = False
        
        # 1. Если начинается новая тема
        if is_new_topic and current_para:
            should_break = True
        
        # 2. Если предложение очень длинное
        if is_long and current_para:
            should_break = True
        
        # 3. Если в абзаце уже 3-5 предложений и длина > 300 символов
        if len(current_para) >= 3 and current_len > 300 and is_new_topic:
            should_break = True
        
        # 4. Если в абзаце больше 5 предложений
        if len(current_para) >= 5:
            should_break = True
        
        # Если нужно разорвать
        if should_break and current_para:
            paragraphs.append(' '.join(current_para))
            current_para = []
            current_len = 0
        
        # Добавляем предложение в текущий абзац
        current_para.append(sent)
        current_len += len(sent)
    
    # Добавляем последний абзац
    if current_para:
        paragraphs.append(' '.join(current_para))
    
    # Проверяем результат
    if len(paragraphs) < 2 and len(sentences) > 10:
        return force_split_by_length(text)
    
    return '\n\n'.join(paragraphs)


def force_split_by_length(text: str, max_chars: int = 450) -> str:
    """Принудительное разбиение по длине (запасной вариант)."""
    if not text or len(text) < 200:
        return text
    
    words = text.split()
    if len(words) < 20:
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
    
    if len(paragraphs) == 1 and len(words) > 30:
        mid = len(words) // 2
        paragraphs = [
            ' '.join(words[:mid]),
            ' '.join(words[mid:])
        ]
    
    return '\n\n'.join(paragraphs)


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

                # 1. Цепочка переводов
                logger.info("Step 1: Translation chain...")
                processed_text = apply_translation_chain_full(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 40, "log": "Переводы завершены"})

                # 2. Очистка артефактов
                logger.info("Step 2: Cleaning artifacts...")
                processed_text = clean_translation_artifacts(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 60, "log": "Артефакты удалены"})

                # 3. Интеллектуальное разбиение на абзацы (с выделением диалогов)
                logger.info("Step 3: Intelligent paragraph splitting...")
                processed_text = split_into_paragraphs_by_logic(processed_text)
                para_count = len(processed_text.split('\n\n'))
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 85, "log": f"Абзацев: {para_count}"})

                # 4. Финальная полировка
                logger.info("Step 4: Final polish...")
                processed_text = apply_light_polish(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100, "log": "Готово!"})

                final_para_count = len(processed_text.split('\n\n'))
                logger.info(f"Final: {len(processed_text)} chars, {final_para_count} paragraphs")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через цепочку переводов. Абзацев: {final_para_count}",
                    "changes": [
                        "Переведён через Google Translate (RU→JA→FI→EN→RU)",
                        f"Разделён на {final_para_count} логических абзацев"
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
