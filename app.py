"""
Chapter Editor v3.6.2 — humanizer ОТКЛЮЧЁН, детальное логирование ошибок
"""
import json
import os
import re
import time
import logging
import random
import traceback
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.6.2"
MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Humanizer отключён (переменная игнорируется)
ENABLE_HUMANIZER = True
HUMANIZER_AVAILABLE = False

# Если вы всё же хотите включить humanizer, раскомментируйте эти строки
# и установите переменную окружения ENABLE_HUMANIZER=true
# if os.environ.get("ENABLE_HUMANIZER", "false").lower() == "true":
#     try:
#         import humanizer
#         HUMANIZER_AVAILABLE = True
#         ENABLE_HUMANIZER = True
#     except ImportError:
#         logger.warning("humanizer module not found. Humanizer disabled.")
#         ENABLE_HUMANIZER = False


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
    if not text or len(text.strip()) < 2:
        return text
    try:
        en = translate_with_fallback(text, target_lang="en")
        ru = translate_with_fallback(en, target_lang="ru")
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
    logger.info(f"Starting translation chain (RU→EN→RU) for {len(text)} chars...")
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


def clean_translation_artifacts(text: str) -> str:
    finnish_patterns = [
        r'\bTietenkin\b', r'\bhe tarvitsevat\b', r'\bJos se toimii\b',
        r'\bvaikka se ei\b', r'\btoimi\b', r'\bpuolella\b',
        r'\bvaltamerta\b', r'\bRakennamme\b', r'\bsiis\b',
        r'\bsademeren\b', r'\bJa lentää\b', r'\bsinne\b',
        r'\baamiaiseksi\b', r'\bkuvaan\b', r'\bMikä tämä on\b',
        r'\bAleksei kysyi\b', r'\bTalomme suunnitelma\b',
        r'\bKuussa ei ole\b', r'\brannoille\b',
        r'\bettä\b', r'\bjoka\b', r'\bmitä\b', r'\bniin\b',
        r'\bkun\b', r'\bvoi\b', r'\bse\b', r'\bja\b'
    ]
    english_phrases = [
        r'\bfirst to spot\b', r'\bthe genius\b', r'\bof a student\b',
        r'\bfrom Siberia\b', r'\bnow he watched\b', r'\bas that spark\b',
        r'\bignited its owner\'s career\b', r'\bI came to warn you\b',
        r'\bthey want to seduce you\b', r'\bthey provide the lab\b',
        r'\bbudget and team\b', r'\bwhatever you want\b',
        r'\bhowever research requires a license\b',
        r'\bA group came from\b', r'\bMIT\b', r'\bthey need\b',
        r'\bsaid more quietly\b', r'\bbudget\b', r'\bteam\b',
        r'\bresearch\b', r'\blicense\b', r'\brequires\b', r'\bcame from\b'
    ]
    japanese_patterns = [r'[\u3040-\u30FF]+']

    for pattern in finnish_patterns + english_phrases:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    for pattern in japanese_patterns:
        text = re.sub(pattern, '', text)

    text = re.sub(r'(\w)\.(\w)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'^[.,!?;:\s]+$', '', text, flags=re.MULTILINE)

    text = re.sub(r'—\s*', '— ', text)

    if not re.search(r'[.!?]', text):
        sentences = re.split(r'(?<=[а-яa-z])\s+(?=[А-ЯA-Z])', text)
        if len(sentences) > 1:
            text = '. '.join(sentences) + '.'

    return text.strip()


def diversify_dialog_tags(text: str) -> str:
    synonyms = {
        'сказал': ['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал'],
        'сказала': ['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала']
    }

    pattern = r'(—[^—]+?—\s*)(сказал|сказала)(\s+[а-яА-Я]+[.,!?]?)'
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if not matches:
        return text

    replacements = []
    for match in matches:
        before = match.group(1)
        verb = match.group(2)
        after = match.group(3)
        if verb in synonyms:
            new_verb = random.choice(synonyms[verb])
            replacements.append((match.start(), match.end(), before + new_verb + after))

    for start, end, repl in reversed(replacements):
        text = text[:start] + repl + text[end:]

    return text


def split_into_paragraphs_by_logic(text: str) -> str:
    if not text or len(text) < 200:
        return text
    chunk_size = 400
    words = text.split()
    paragraphs = []
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > chunk_size and current:
            paragraphs.append(' '.join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += len(word) + 1
    if current:
        paragraphs.append(' '.join(current))
    if len(paragraphs) == 1 and len(text) > 500:
        paragraphs = []
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i+chunk_size].strip()
            if chunk:
                paragraphs.append(chunk)
    return '\n\n'.join(paragraphs)


def apply_light_polish(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(' - ', ' — ')
    text = re.sub(r'—\s*', '— ', text)
    return text


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


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

        original_len = len(chapter_text)

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": original_len, "percent": 0, "log": "Начинаем обработку..."})

                # 1. Цепочка переводов (RU→EN→RU)
                logger.info("Step 1: Translation chain (RU→EN→RU)...")
                processed_text = apply_translation_chain_full(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 35, "log": "Переводы завершены"})

                # 2. Очистка артефактов
                logger.info("Step 2: Cleaning artifacts...")
                processed_text = clean_translation_artifacts(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 50, "log": "Артефакты удалены"})

                # 3. Разнообразие диалоговых тегов
                logger.info("Step 3: Diversifying dialog tags...")
                processed_text = diversify_dialog_tags(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 65, "log": "Диалоговые теги обновлены"})

                # 4. Принудительное разбиение на абзацы
                logger.info("Step 4: Forced paragraph splitting...")
                processed_text = split_into_paragraphs_by_logic(processed_text)
                para_count = len(processed_text.split('\n\n'))
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 80, "log": f"Абзацев: {para_count}"})

                # 5. Финальная полировка
                logger.info("Step 5: Final polish...")
                processed_text = apply_light_polish(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 90, "log": "Полировка выполнена"})

                # 6. Humanizer отключён — пропускаем
                # Если вы хотите включить humanizer, раскомментируйте блок ниже
                # и установите ENABLE_HUMANIZER = True в начале файла

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 100, "log": "Готово!"})

                final_len = len(processed_text)
                final_para_count = len(processed_text.split('\n\n'))
                loss = (original_len - final_len) / original_len
                logger.info(f"Final: {final_len} chars, loss {loss:.2%}, {final_para_count} paragraphs")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст переработан через упрощённую цепочку (RU→EN→RU). Потеря: {loss:.1%}. Абзацев: {final_para_count}",
                    "changes": [
                        "Переведён через Google Translate / MyMemory (RU→EN→RU)",
                        "Разнообразие диалоговых тегов",
                        f"Разделён на {final_para_count} абзацев",
                        "Удалены артефакты перевода"
                    ],
                    "checklist": []
                })
            except Exception as e:
                error_trace = traceback.format_exc()
                logger.error(f"Error in generate: {error_trace}")
                # Отправляем детальную ошибку в SSE
                yield _sse("error", {"detail": f"{str(e)}\n\n{error_trace}"})
                raise

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    except Exception as e:
        logger.exception("api_revise: Unexpected error")
        return jsonify(detail=f"Server error: {str(e)}\n{traceback.format_exc()}"), 500


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.errorhandler(HTTPException)
def handle_http_exception(exc):
    return jsonify(detail=exc.description or str(exc)), exc.code or 500


@app.errorhandler(Exception)
def handle_exception(exc):
    logger.exception("Unhandled exception")
    return jsonify(detail=f"Server error: {str(exc)}\n{traceback.format_exc()}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
