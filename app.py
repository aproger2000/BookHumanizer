"""
Chapter Editor v3.2.5 — Humanization via Translation Chain (интеллектуальное разбиение на абзацы)
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

APP_VERSION = "3.2.5"

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


def process_paragraph_through_chain(paragraph: str) -> str:
    """Обрабатывает один абзац через цепочку переводов."""
    if not paragraph or len(paragraph.strip()) < 2:
        return paragraph
    
    try:
        ja = translate_text(paragraph, target_lang="ja")
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
    logger.info(f"Found {len(paragraphs)} paragraphs")
    
    processed_paragraphs = []
    
    for i, para in enumerate(paragraphs):
        if not para.strip():
            processed_paragraphs.append(para)
            continue
        
        logger.info(f"Processing paragraph {i+1}/{len(paragraphs)}...")
        
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
                processed = process_paragraph_through_chain(chunk)
                processed_chunks.append(processed)
            
            processed_paragraphs.append(" ".join(processed_chunks))
        else:
            processed_paragraphs.append(process_paragraph_through_chain(para))
    
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
    
    # Убираем лишние точки внутри слов
    text = re.sub(r'(\w)\.(\w)', r'\1\2', text)
    
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()


def split_into_paragraphs_by_logic(text: str) -> str:
    """
    Разбивает текст на абзацы по логике изложения.
    Анализирует: диалоги, смену тем, длину предложений.
    """
    if not text or len(text) < 200:
        return text
    
    # Если уже есть абзацы, проверяем их качество
    existing = text.split('\n\n')
    if len(existing) >= 3 and all(len(p.strip()) > 50 for p in existing):
        # Уже хорошо разбито
        return text
    
    # Разбиваем по предложениям (по точкам, вопросительным и восклицательным знакам)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Если предложений мало — принудительное разбиение
    if len(sentences) < 5:
        return force_split_by_length(text)
    
    paragraphs = []
    current_para = []
    current_len = 0
    dialog_count = 0
    is_in_dialog = False
    
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        
        # Проверяем, является ли предложение диалогом
        is_dialog = sent.startswith('"') or sent.startswith('«') or sent.startswith('—')
        
        # Проверяем, не начинается ли предложение с новой темы (имя, местоимение)
        is_new_topic = False
        topic_words = ['Алексей', 'Масарик', 'Анна', 'Кросс', 'Он', 'Она', 'Они', 'Ибис']
        for word in topic_words:
            if sent.startswith(word):
                is_new_topic = True
                break
        
        # Проверяем длину предложения
        sent_len = len(sent)
        is_long = sent_len > 200
        is_short = sent_len < 30
        
        # Логика создания нового абзаца:
        should_break = False
        
        # 1. Если начинается новый диалог
        if is_dialog and not is_in_dialog and current_para:
            should_break = True
        
        # 2. Если заканчивается диалог и начинается новая тема
        if is_in_dialog and is_new_topic and current_para:
            should_break = True
        
        # 3. Если предложение очень длинное (>200 символов)
        if is_long and current_para:
            should_break = True
        
        # 4. Если в абзаце уже 3-5 предложений и длина > 300 символов
        if len(current_para) >= 3 and current_len > 300 and (is_new_topic or is_dialog):
            should_break = True
        
        # 5. Если в абзаце больше 5 предложений
        if len(current_para) >= 5:
            should_break = True
        
        # Если нужно разорвать
        if should_break and current_para:
            paragraphs.append(' '.join(current_para))
            current_para = []
            current_len = 0
            is_in_dialog = False
        
        # Добавляем предложение в текущий абзац
        current_para.append(sent)
        current_len += sent_len
        
        if is_dialog:
            is_in_dialog = True
            dialog_count += 1
    
    # Добавляем последний абзац
    if current_para:
        paragraphs.append(' '.join(current_para))
    
    # Проверяем результат
    result = '\n\n'.join(paragraphs)
    
    # Если абзацев всё ещё мало — применяем принудительное разбиение
    if len(paragraphs) < 2 and len(sentences) > 10:
        return force_split_by_length(text)
    
    return result


def force_split_by_length(text: str, max_paragraph_chars: int = 450) -> str:
    """
    ПРИНУДИТЕЛЬНО разбивает текст на абзацы по количеству символов.
    Используется как запасной вариант.
    """
    words = text.split()
    if len(words) < 20:
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
    
    # Если получился один абзац, но слов много — разбиваем
    if len(paragraphs) == 1 and len(words) > 30:
        mid = len(words) // 2
        paragraphs = [
            ' '.join(words[:mid]),
            ' '.join(words[mid:])
        ]
    
    return '\n\n'.join(paragraphs)


def apply_light_polish(text: str) -> str:
    """Лёгкая пост-обработка для естественности."""
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
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0})

                logger.info("Applying translation chain...")
                processed_text = apply_translation_chain_with_paragraphs(chapter_text)
                
                processed_text = clean_translation_artifacts(processed_text)
                
                # Интеллектуальное разбиение на абзацы
                processed_text = split_into_paragraphs_by_logic(processed_text)
                
                processed_text = apply_light_polish(processed_text)

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100})

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": "Текст переработан через цепочку переводов",
                    "changes": [
                        "Переведён через Google Translate (RU→JA→FI→EN→RU)",
                        "Разделён на абзацы по логике изложения"
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
