"""
Chapter Editor v3.2.6 — Humanization via Translation Chain (с обходом кеша и детальным логированием)
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

APP_VERSION = "3.2.6"

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
    """Переводит текст через публичный API Google Translate с обходом кеша."""
    if not text or len(text.strip()) < 2:
        return text
    
    try:
        # Добавляем случайный параметр для обхода кеша
        cache_buster = random.randint(100000, 999999)
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text,
            "cb": cache_buster  # Обход кеша
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


def process_paragraph_through_chain(paragraph: str, step_num: int = 0) -> str:
    """Обрабатывает один абзац через цепочку переводов с логированием."""
    if not paragraph or len(paragraph.strip()) < 2:
        return paragraph
    
    try:
        logger.info(f"[Step {step_num}] RU->JA: {len(paragraph)} chars")
        ja = translate_text(paragraph, target_lang="ja")
        logger.info(f"[Step {step_num}] JA->FI: {len(ja)} chars")
        fi = translate_text(ja, target_lang="fi")
        logger.info(f"[Step {step_num}] FI->EN: {len(fi)} chars")
        en = translate_text(fi, target_lang="en")
        logger.info(f"[Step {step_num}] EN->RU: {len(en)} chars")
        ru = translate_text(en, target_lang="ru")
        logger.info(f"[Step {step_num}] FINAL: {len(ru)} chars")
        return ru
    except Exception as e:
        logger.error(f"Paragraph processing error: {e}")
        return paragraph


def apply_translation_chain_with_paragraphs(text: str, progress_callback=None) -> str:
    """Обрабатывает текст, сохраняя структуру абзацев, с прогрессом."""
    logger.info(f"Starting translation chain for {len(text)} chars...")
    
    paragraphs = text.split('\n\n')
    logger.info(f"Found {len(paragraphs)} paragraphs")
    
    processed_paragraphs = []
    total = len(paragraphs)
    
    for i, para in enumerate(paragraphs):
        if not para.strip():
            processed_paragraphs.append(para)
            continue
        
        logger.info(f"Processing paragraph {i+1}/{total}...")
        
        if progress_callback:
            progress_callback(i, total, "Перевод абзаца...")
        
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
            for j, chunk in enumerate(chunks):
                logger.info(f"  Chunk {j+1}/{len(chunks)}")
                if progress_callback:
                    progress_callback(i, total, f"Часть {j+1}/{len(chunks)}")
                processed = process_paragraph_through_chain(chunk, i*10+j)
                processed_chunks.append(processed)
            
            processed_paragraphs.append(" ".join(processed_chunks))
        else:
            processed_paragraphs.append(process_paragraph_through_chain(para, i))
        
        if progress_callback:
            progress_callback(i+1, total, f"Готово {i+1}/{total}")
    
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


def split_into_paragraphs_by_logic(text: str) -> str:
    """Разбивает текст на абзацы по логике изложения."""
    logger.info(f"split_into_paragraphs_by_logic: input length {len(text)}")
    
    if not text or len(text) < 200:
        logger.info("Text too short, no splitting")
        return text
    
    # Если уже есть абзацы
    existing = text.split('\n\n')
    if len(existing) >= 3 and all(len(p.strip()) > 50 for p in existing):
        logger.info(f"Already has {len(existing)} good paragraphs")
        return text
    
    # Разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text)
    logger.info(f"Found {len(sentences)} sentences")
    
    if len(sentences) < 5:
        logger.info("Few sentences, using force split")
        return force_split_by_length(text)
    
    paragraphs = []
    current_para = []
    current_len = 0
    is_in_dialog = False
    
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        
        is_dialog = sent.startswith('"') or sent.startswith('«') or sent.startswith('—')
        is_new_topic = any(sent.startswith(w) for w in ['Алексей', 'Масарик', 'Анна', 'Кросс', 'Он', 'Она'])
        is_long = len(sent) > 200
        
        # Логика разрыва
        should_break = False
        if is_dialog and not is_in_dialog and current_para:
            should_break = True
        elif is_in_dialog and is_new_topic and current_para:
            should_break = True
        elif is_long and current_para:
            should_break = True
        elif len(current_para) >= 3 and current_len > 300 and (is_new_topic or is_dialog):
            should_break = True
        elif len(current_para) >= 5:
            should_break = True
        
        if should_break and current_para:
            paragraphs.append(' '.join(current_para))
            current_para = []
            current_len = 0
            is_in_dialog = False
        
        current_para.append(sent)
        current_len += len(sent)
        if is_dialog:
            is_in_dialog = True
    
    if current_para:
        paragraphs.append(' '.join(current_para))
    
    logger.info(f"Split into {len(paragraphs)} paragraphs")
    
    # Проверяем результат
    if len(paragraphs) < 2 and len(sentences) > 10:
        logger.info("Too few paragraphs, using force split")
        return force_split_by_length(text)
    
    result = '\n\n'.join(paragraphs)
    logger.info(f"Result length: {len(result)}")
    return result


def force_split_by_length(text: str, max_paragraph_chars: int = 450) -> str:
    """Принудительно разбивает текст на абзацы по количеству символов."""
    logger.info(f"force_split_by_length: input length {len(text)}")
    
    words = text.split()
    if len(words) < 20:
        logger.info("Too few words, no splitting")
        return text
    
    paragraphs = []
    current = []
    current_len = 0
    
    for word in words:
        word_len = len(word) + 1
        if current_len > max_paragraph_chars and len(current) >= 3:
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
    
    logger.info(f"force_split: {len(paragraphs)} paragraphs")
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
            full_log = []
            full_log.append("=== НАЧАЛО ОБРАБОТКИ ===")
            full_log.append(f"Исходный текст: {len(chapter_text)} символов")
            
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0, "log": "Начинаем обработку..."})

                full_log.append("1. Запуск цепочки переводов...")
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 5, "log": "Цепочка переводов: RU→JA→FI→EN→RU"})
                
                def progress_callback(current, total, message):
                    pct = 10 + int((current / total) * 60)
                    yield _sse("progress", {
                        "chars": current,
                        "estimated_total": total,
                        "percent": pct,
                        "log": f"Перевод: {message}"
                    })
                
                # Используем генератор для прогресса
                processed_text = apply_translation_chain_with_paragraphs(chapter_text)
                
                full_log.append(f"2. После переводов: {len(processed_text)} символов")
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 70, "log": "Переводы завершены"})
                
                full_log.append("3. Очистка артефактов...")
                processed_text = clean_translation_artifacts(processed_text)
                full_log.append(f"   После очистки: {len(processed_text)} символов")
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 80, "log": "Артефакты удалены"})
                
                full_log.append("4. Разбиение на абзацы...")
                processed_text = split_into_paragraphs_by_logic(processed_text)
                full_log.append(f"   После разбиения: {len(processed_text)} символов")
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 90, "log": "Абзацы сформированы"})
                
                full_log.append("5. Финальная полировка...")
                processed_text = apply_light_polish(processed_text)
                full_log.append(f"   Финал: {len(processed_text)} символов")
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100, "log": "Готово!"})

                # Лог разбиения на абзацы
                para_count = len(processed_text.split('\n\n'))
                full_log.append(f"6. Итоговое количество абзацев: {para_count}")
                
                # Добавляем лог в результат
                log_text = "\n".join(full_log)
                
                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через цепочку переводов. Абзацев: {para_count}",
                    "changes": [
                        "Переведён через Google Translate (RU→JA→FI→EN→RU)",
                        f"Разделён на {para_count} абзацев по логике изложения"
                    ],
                    "log": log_text,
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
