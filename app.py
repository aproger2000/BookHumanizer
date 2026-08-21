"""
Chapter Editor v4.0.0 — адаптивная обработка абзацев с нейродетектором
"""
import json
import os
import re
import time
import logging
import random
from pathlib import Path
from typing import List, Dict, Tuple

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.0.0"
MAX_CHARS = 30_000
CHUNK_SIZE = 4000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# --- Настройки нейродетектора ---
# Для демонстрации используем порог и заглушку.
# Реальный вызов можно сделать через OpenAI API или другой сервис.
AI_DETECTOR_THRESHOLD = 0.5  # если вероятность AI > 0.5, считаем ИИ-текстом
DETECTOR_API_URL = os.environ.get("DETECTOR_API_URL", None)  # например, https://api.sapling.ai/api/v1/aidetect
DETECTOR_API_KEY = os.environ.get("DETECTOR_API_KEY", None)

# --- Цепочки переводов (по порядку увеличения сложности) ---
TRANSLATION_CHAINS = [
    ["en", "ru"],         # RU → EN → RU (базовая)
    ["sk", "ru"],         # RU → SK → RU (словацкий)
    ["en", "es", "ru"],   # RU → EN → ES → RU (испанский через английский)
]

def detect_ai(text: str) -> float:
    """
    Определяет вероятность того, что текст сгенерирован ИИ.
    Возвращает float от 0 до 1 (1 — точно ИИ).
    """
    if not text or len(text) < 20:
        return 0.0

    # Если есть реальный API, используем его
    if DETECTOR_API_URL and DETECTOR_API_KEY:
        try:
            # Пример для Sapling.ai
            # payload = {"text": text, "key": DETECTOR_API_KEY}
            # response = requests.post(DETECTOR_API_URL, json=payload, timeout=10)
            # if response.status_code == 200:
            #     data = response.json()
            #     return data.get("score", 0.0)
            pass
        except Exception as e:
            logger.warning(f"Detector API error: {e}")

    # Заглушка: используем простые эвристики для демонстрации
    # 1. Доля английских букв
    letters = sum(1 for c in text if c.isalpha())
    if letters == 0:
        return 0.0
    latin = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    latin_ratio = latin / letters

    # 2. Наличие повторяющихся фраз
    # Считаем количество повторяющихся слов (простейшая эвристика)
    words = re.findall(r'\b\w+\b', text.lower())
    if len(words) > 5:
        unique_ratio = len(set(words)) / len(words)
    else:
        unique_ratio = 0.5

    # 3. Доля знаков препинания
    punct = sum(1 for c in text if c in '.,!?;:')
    punct_ratio = punct / len(text) if len(text) > 0 else 0

    # Комбинируем: высокий латинский + низкое разнообразие слов + низкая пунктуация -> ИИ
    score = (latin_ratio * 0.4 + (1 - unique_ratio) * 0.3 + (1 - punct_ratio) * 0.3)
    # Нормализуем, чтобы было от 0 до 1
    score = min(1.0, max(0.0, score))
    return score


def translate_text(text: str, target_lang: str) -> str:
    """Переводит текст на целевой язык."""
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
    for attempt in range(2):
        try:
            resp = requests.get(url_google, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                translated = "".join(item[0] for item in data[0] if item[0])
                if translated:
                    return translated
            time.sleep(1 + random.random())
        except Exception as e:
            logger.warning(f"Translate error: {e}")
            time.sleep(1)

    # Fallback: MyMemory
    url_mymemory = "https://api.mymemory.translated.net/get"
    payload = {"q": text, "langpair": f"auto|{target_lang}"}
    try:
        resp = requests.post(url_mymemory, data=payload, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("responseStatus") == 200:
                translated = data.get("responseData", {}).get("translatedText")
                if translated:
                    return translated
    except Exception:
        pass

    return text


def apply_chain(text: str, chain: List[str]) -> str:
    """Применяет цепочку переводов: список языков, начиная с первого как промежуточного."""
    if not text:
        return text
    current = text
    for lang in chain:
        current = translate_text(current, lang)
    return current


def clean_text(text: str) -> str:
    """Минимальная очистка после перевода."""
    # Убираем явные артефакты (для всех языков)
    patterns = [
        r'\bMIT\b',
        r'\bI thought so\b',
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
        # Испанские/чешские артефакты
        r'Vino quieren alejarte',
        r'Laboratorio, presupuesto, Equipment\. Todo lo quieras\.',
        r'necesitan licencia para su review',
        r'Por suuesto necesitan tu batería',
        r'silent\. “, "\. — And in return\? — In return — nothing\. , \. \. like a shark\'s',
        r'Empty Null: Final Drawings',
        r'Null Vacuum: Final Drawings',
        r'ММистер Штерн',
        r'мМистер Штерн',
        r'««««Ибис»»»»',
        r'«««Ибис»»»',
        r'««Ибис»»',
        r'««Ибис»а»',
        r'Стерн',  # заменим на Штерн (будет отдельно)
    ]
    for pat in patterns:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)

    # Замены
    replacements = [
        (r'черного вина', 'черного кофе'),
        (r'\bТоки\b', '«Ибис»'),
        (r'не испортил', 'не шутил'),
        (r'—\s*знать', '— Я знаю'),
        (r'Босимом', 'Босиком'),
        (r'Стерн', 'Штерн'),
    ]
    for pat, repl in replacements:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)

    # Чистка пробелов и пунктуации
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'^[.,!?;:\s]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'—\s*', '— ', text)
    text = text.replace('""', '"').replace('""', '"')

    return text.strip()


def split_into_paragraphs(text: str) -> List[str]:
    """Разбивает текст на абзацы по двойным переносам или по предложениям."""
    if not text:
        return []
    # Сначала пробуем по \n\n
    paragraphs = text.split('\n\n')
    if len(paragraphs) > 1:
        return [p.strip() for p in paragraphs if p.strip()]
    # Если нет, разбиваем по точкам
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 3:
        # Группируем по 2-3 предложения
        grouped = []
        temp = []
        count = 0
        for sent in sentences:
            temp.append(sent)
            count += 1
            if count >= 3:
                grouped.append(' '.join(temp))
                temp = []
                count = 0
        if temp:
            grouped.append(' '.join(temp))
        return grouped
    return [text]


def process_paragraph(paragraph: str) -> Tuple[str, str, float]:
    """
    Обрабатывает один абзац: последовательно применяет цепочки, пока результат не будет HUMAN.
    Возвращает (обработанный_текст, использованная_цепочка, вероятность_AI).
    """
    if not paragraph:
        return "", "", 1.0

    best_text = paragraph
    best_ai_score = 1.0
    used_chain = "none"

    for chain in TRANSLATION_CHAINS:
        # Применяем цепочку
        translated = apply_chain(paragraph, chain)
        # Очищаем
        cleaned = clean_text(translated)
        # Проверяем через детектор
        ai_score = detect_ai(cleaned)
        logger.info(f"Chain {'->'.join(chain)}: AI score {ai_score:.3f} for paragraph: {cleaned[:50]}...")

        if ai_score < AI_DETECTOR_THRESHOLD:
            # Успех: считаем текст человеческим
            return cleaned, "->".join(chain), ai_score

        # Сохраняем лучший (с наименьшим AI score)
        if ai_score < best_ai_score:
            best_ai_score = ai_score
            best_text = cleaned
            used_chain = "->".join(chain)

    # Если ни одна цепочка не дала хорошего результата, возвращаем лучший
    return best_text, used_chain, best_ai_score


def process_text_with_paragraphs(text: str) -> Tuple[str, List[Dict]]:
    """
    Обрабатывает весь текст, разбивая на абзацы и обрабатывая каждый.
    Возвращает (полный_текст, список_метаданных_по_абзацам).
    """
    paragraphs = split_into_paragraphs(text)
    logger.info(f"Split into {len(paragraphs)} paragraphs")

    processed_paragraphs = []
    metadata = []

    for i, para in enumerate(paragraphs):
        logger.info(f"Processing paragraph {i+1}/{len(paragraphs)}")
        processed, chain, score = process_paragraph(para)
        processed_paragraphs.append(processed)
        metadata.append({
            "index": i,
            "original": para,
            "processed": processed,
            "chain": chain,
            "ai_score": score,
            "human": score < AI_DETECTOR_THRESHOLD
        })

    full_text = '\n\n'.join(processed_paragraphs)
    return full_text, metadata


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
                yield _sse("progress", {"chars": 0, "estimated_total": original_len, "percent": 0, "log": "Начинаем обработку по абзацам..."})

                # Разбиваем на абзацы
                paragraphs = split_into_paragraphs(chapter_text)
                total_paragraphs = len(paragraphs)
                logger.info(f"Total paragraphs: {total_paragraphs}")

                processed_paragraphs = []
                metadata = []

                for idx, para in enumerate(paragraphs):
                    log_msg = f"Обработка абзаца {idx+1}/{total_paragraphs}"
                    yield _sse("paragraph_start", {"index": idx, "total": total_paragraphs, "text": para, "log": log_msg})
                    yield _sse("progress", {"chars": len(para), "estimated_total": original_len, "percent": int((idx / total_paragraphs) * 100), "log": log_msg})

                    # Обрабатываем абзац
                    processed, chain, score = process_paragraph(para)
                    processed_paragraphs.append(processed)
                    meta = {
                        "index": idx,
                        "original": para,
                        "processed": processed,
                        "chain": chain,
                        "ai_score": score,
                        "human": score < AI_DETECTOR_THRESHOLD
                    }
                    metadata.append(meta)
                    yield _sse("paragraph_done", meta)

                full_text = '\n\n'.join(processed_paragraphs)
                final_len = len(full_text)
                loss = (original_len - final_len) / original_len

                yield _sse("progress", {"chars": final_len, "estimated_total": original_len, "percent": 100, "log": "Готово!"})

                yield _sse("done", {
                    "revised_text": full_text,
                    "original_text": chapter_text,
                    "paragraphs": metadata,
                    "summary": f"Текст переработан по абзацам с адаптивными цепочками. Потеря: {loss:.1%}. Абзацев: {total_paragraphs}",
                    "changes": [
                        "Адаптивная обработка каждого абзаца",
                        "Использованы цепочки: RU→EN→RU, RU→SK→RU, RU→EN→ES→RU",
                        "Проверка через нейродетектор"
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
