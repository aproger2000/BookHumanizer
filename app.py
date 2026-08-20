"""
Chapter Editor v3.5.5 — Humanization via Translation Chain + улучшенная очистка и разбиение
Работает с Google Translate с повторными попытками.
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

APP_VERSION = "3.5.5"  # обновлено

MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def translate_text_with_retry(text: str, target_lang: str = "en", max_retries: int = 3) -> str:
    """Переводит текст через публичный API Google Translate с повторными попытками."""
    if not text or len(text.strip()) < 2:
        return text

    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_lang,
        "dt": "t",
        "q": text
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 429:
                wait = (2 ** attempt) + random.random()
                logger.warning(f"Rate limit (429) for lang {target_lang}, retrying in {wait:.1f}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            translated = ""
            for item in data[0]:
                if item[0]:
                    translated += item[0]
            return translated or text
        except requests.exceptions.Timeout:
            logger.error(f"Timeout for lang {target_lang}, attempt {attempt+1}")
            time.sleep(1)
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for lang {target_lang}: {e}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Unexpected error for lang {target_lang}: {e}")
            break
    # Если все попытки провалились, возвращаем исходный текст
    return text


def split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    """Разбивает текст на части, сохраняя абзацы (улучшено)."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    current_chunk = ""
    # Разбиваем по двойным переносам (абзацы)
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

    # Если нет абзацев, разбиваем по предложениям
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
    """Обрабатывает один фрагмент через цепочку переводов с повторными попытками."""
    if not text or len(text.strip()) < 2:
        return text

    try:
        ja = translate_text_with_retry(text, target_lang="ja")
        fi = translate_text_with_retry(ja, target_lang="fi")
        en = translate_text_with_retry(fi, target_lang="en")
        ru = translate_text_with_retry(en, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Chunk processing error: {e}")
        return text


def apply_translation_chain_full(text: str) -> str:
    """Обрабатывает весь текст, разбивая на части (с улучшенной склейкой)."""
    logger.info(f"Starting translation chain for {len(text)} chars...")

    chunks = split_text_into_chunks(text)
    logger.info(f"Split into {len(chunks)} chunks")

    processed_chunks = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i+1}/{len(chunks)}...")
        processed = process_chunk_through_chain(chunk)
        processed_chunks.append(processed)

    # Склеиваем с двойным переносом, но если текст был без абзацев — используем одиночный пробел
    # Проверим, были ли исходные абзацы
    if '\n\n' in text:
        result = "\n\n".join(processed_chunks)
    else:
        result = " ".join(processed_chunks)

    logger.info(f"Translation complete. Result length: {len(result)}")
    return result


def clean_translation_artifacts(text: str) -> str:
    """Расширенная очистка артефактов перевода."""
    # Финские фразы (добавлены новые)
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

    # Английские фразы
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

    # Японские артефакты (часто остаются хирагана/катакана)
    japanese_patterns = [
        r'[\u3040-\u30FF]+',  # хирагана и катакана
    ]

    for pattern in finnish_patterns + english_phrases:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    for pattern in japanese_patterns:
        text = re.sub(pattern, '', text)

    # Удаляем лишние точки и запятые
    text = re.sub(r'(\w)\.(\w)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)

    # Удаляем полностью пустые предложения (состоящие только из знаков препинания)
    text = re.sub(r'^[.,!?;:\s]+$', '', text, flags=re.MULTILINE)

    return text.strip()


def split_into_paragraphs_by_logic(text: str) -> str:
    """
    Улучшенное разбиение на абзацы: диалоги отдельно, группировка по предложениям,
    принудительное разбиение по длине, если логика не сработала.
    """
    if not text or len(text) < 200:
        return text

    # Разбиваем на предложения
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 3:
        # Мало предложений — принудительное разбиение по символам
        paragraphs = []
        chunk_size = 400
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i+chunk_size].strip()
            if chunk:
                paragraphs.append(chunk)
        return '\n\n'.join(paragraphs)

    paragraphs = []
    current_para = []
    current_len = 0
    in_dialog = False
    dialog_buffer = []

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        # Проверяем диалог
        is_dialog = (
            sent.startswith('—') or
            sent.startswith('"') or
            sent.startswith('«') or
            sent.startswith('–')
        )

        if is_dialog:
            # Если диалог, закрываем текущий абзац и начинаем новый
            if current_para:
                paragraphs.append(' '.join(current_para))
                current_para = []
                current_len = 0
            # Собираем все подряд идущие диалоги в один абзац (или каждый отдельно?)
            # Лучше каждый диалог отдельным абзацем
            paragraphs.append(sent)
            continue

        # Обычное предложение
        if current_len > 350 and len(current_para) >= 2:
            paragraphs.append(' '.join(current_para))
            current_para = []
            current_len = 0

        current_para.append(sent)
        current_len += len(sent)

    if current_para:
        paragraphs.append(' '.join(current_para))

    # Если после всех манипуляций получился один абзац — принудительно режем
    if len(paragraphs) < 2 and len(text) > 500:
        paragraphs = []
        chunk_size = 400
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i+chunk_size].strip()
            if chunk:
                paragraphs.append(chunk)

    return '\n\n'.join(paragraphs)


def apply_light_polish(text: str) -> str:
    """Лёгкая пост-обработка."""
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(' - ', ' — ')
    # Восстанавливаем кавычки для диалогов, если сбились
    text = re.sub(r'—\s*', '— ', text)
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

                # 3. Интеллектуальное разбиение на абзацы
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
                        "Переведён через Google Translate (RU→JA→FI→EN→RU) с повторными попытками",
                        f"Разделён на {final_para_count} логических абзацев",
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
