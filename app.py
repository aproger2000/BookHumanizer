"""
Chapter Editor v3.9.12 — парсинг нейродетектора Яндекса (опционально)
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

# Для парсинга
try:
    from bs4 import BeautifulSoup
    BS_AVAILABLE = True
except ImportError:
    BS_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.9.12"
MAX_CHARS = 30_000
CHUNK_SIZE = 3000

# Опция: парсить ли нейродетектор Яндекса
ENABLE_YANDEX_PARSER = os.environ.get("ENABLE_YANDEX_PARSER", "false").lower() == "true"
logger.info(f"Yandex NeuroDetector parser enabled: {ENABLE_YANDEX_PARSER}")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# === ПЕРЕВОДЧИК ===
def translate_with_fallback(text: str, target_lang: str = "en") -> str:
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

    try:
        resp = requests.get(url_google, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            translated = "".join(item[0] for item in data[0] if item[0])
            if translated:
                return translated
        logger.warning(f"Google failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Google exception: {e}")

    try:
        url_mymemory = "https://api.mymemory.translated.net/get"
        payload = {
            "q": text,
            "langpair": f"auto|{target_lang}",
            "de": "user@example.com"
        }
        resp = requests.post(url_mymemory, data=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("responseStatus") == 200:
                translated = data.get("responseData", {}).get("translatedText")
                if translated:
                    return translated
        logger.warning(f"MyMemory failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"MyMemory exception: {e}")

    return text


def translate_chunk(text: str, chain: list) -> str:
    if not text or len(text.strip()) < 2:
        return text
    try:
        current = text
        for lang in chain:
            current = translate_with_fallback(current, target_lang=lang)
        ru = translate_with_fallback(current, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Chain {chain} error: {e}")
        return text


def split_paragraphs(text: str) -> list:
    if not text:
        return []
    text = text.replace('\r\n', '\n')
    paragraphs = text.split('\n\n')
    return [p.strip() for p in paragraphs if p.strip()]


# === ВСТРОЕННЫЙ ДЕТЕКТОР ===
def get_human_score(text: str) -> int:
    if not text or len(text) < 20:
        return 50

    letters = sum(1 for ch in text if ch.isalpha())
    if letters == 0:
        return 80
    latin_count = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
    latin_ratio = latin_count / letters
    score = max(0, 100 - (latin_ratio * 120))

    markers = [
        r'\bI thought so\b', r'\bNo bureaucracy\b', r'\bNo grant fees\b',
        r'\bIn return nothing\b', r'\bfrom the beginning\b',
        r'\bAlexey remained silent\b', r'\bCross continued\b',
        r'\bfunds\?', r'\bthe offers will become\b', r'\bless and less\b',
        r'\bpolite\b', r'\byou continue to work\b',
        r'\bwe provide you with peace of mind\b', r'\bwhen the world changes\b',
        r'\bwe\'d like you to remember\b', r'\bwho your friends were\b',
        r'Vino quieren alejarte', r'Laboratorio, presupuesto',
        r'Empty Null: Final Drawings', r'««««Ибис»»»»',
    ]
    marker_penalty = 0
    for m in markers:
        if re.search(m, text, flags=re.IGNORECASE):
            marker_penalty += 15
    score -= marker_penalty

    words = re.findall(r'[а-яА-Яa-zA-Z]+', text)
    if words:
        avg_len = sum(len(w) for w in words) / len(words)
        if avg_len < 3 or avg_len > 12:
            score -= 10

    word_counts = {}
    for w in words:
        w_lower = w.lower()
        word_counts[w_lower] = word_counts.get(w_lower, 0) + 1
    max_repeat = max(word_counts.values()) if word_counts else 0
    if max_repeat > len(words) * 0.15:
        score -= 15

    return max(0, min(100, int(score)))


def process_paragraph(paragraph: str, style: str = "neutral") -> dict:
    if not paragraph:
        return {"original": paragraph, "revised": paragraph, "status": "error", "chain": "none", "human_score": 0}

    chains = [
        {"name": "EN", "langs": ["en"]},
    ]
    if len(paragraph) < 50:
        chains.append({"name": "EN→CS→ES→IT→FR", "langs": ["en", "cs", "es", "it", "fr"]})

    best_result = None
    best_score = 0

    for chain in chains:
        logger.info(f"Testing chain {chain['name']} on paragraph: {paragraph[:50]}...")
        revised = translate_chunk(paragraph, chain["langs"])
        revised = re.sub(r'Vino quieren alejarte|Laboratorio, presupuesto|Empty Null: Final Drawings', '', revised)
        revised = re.sub(r'««««Ибис»»»»', '«Ибис»', revised)
        revised = revised.strip()
        if revised and len(revised) > 0:
            score = get_human_score(revised)
            logger.info(f"Chain {chain['name']} score: {score}")
            if score > best_score:
                best_score = score
                best_result = {
                    "original": paragraph,
                    "revised": revised,
                    "status": "done" if score > 50 else "error",
                    "chain": chain["name"],
                    "human_score": score
                }
            if score >= 70:
                break

    if best_result:
        return best_result

    return {
        "original": paragraph,
        "revised": paragraph,
        "status": "error",
        "chain": "none",
        "human_score": 0
    }


def analyze_overall(text: str) -> dict:
    if not text or len(text) < 100:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    segments = re.split(r'(?<=[.!?])\s+', text)
    if len(segments) < 3:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    results = {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0}
    for seg in segments:
        if len(seg) < 15:
            continue
        score = get_human_score(seg)
        if score < 30:
            results["AI"] += 1
        elif score < 50:
            results["LIKELY_AI"] += 1
        elif score < 70:
            results["LIKELY_HUMAN"] += 1
        else:
            results["HUMAN"] += 1

    total = sum(results.values())
    if total == 0:
        return {"AI": 0, "LIKELY_AI": 0, "LIKELY_HUMAN": 0, "HUMAN": 0, "score": 0}

    for k in results:
        results[k] = int(results[k] / total * 100)

    score = results["HUMAN"] * 1.0 + results["LIKELY_HUMAN"] * 0.7 + results["LIKELY_AI"] * 0.3
    results["score"] = int(score)
    return results


# === ПАРСИНГ НЕЙРОДЕТЕКТОРА ЯНДЕКСА (опционально) ===
def parse_yandex_neurodetector(text: str) -> dict:
    """
    Отправляет текст на https://yandex.ru/lab/neurodetector и парсит результат.
    Возвращает словарь с категориями или None при ошибке.
    """
    if not text or len(text) < 20:
        return None

    if not BS_AVAILABLE:
        logger.warning("BeautifulSoup not installed, skipping Yandex parser.")
        return None

    # URL страницы и эндпоинт для отправки (найден экспериментально)
    # Важно: структура страницы может меняться, поэтому этот код — временное решение.
    # При обновлении сайта парсер сломается.

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
        'Referer': 'https://yandex.ru/lab/neurodetector',
    })

    try:
        # 1. Получить главную страницу для CSRF-токена
        main_resp = session.get('https://yandex.ru/lab/neurodetector', timeout=10)
        main_resp.raise_for_status()
        soup = BeautifulSoup(main_resp.text, 'html.parser')

        # Попытка найти CSRF-токен (обычно в скрытых полях формы или в meta)
        csrf_token = None
        csrf_input = soup.find('input', {'name': 'csrf_token'})
        if csrf_input:
            csrf_token = csrf_input.get('value')
        if not csrf_token:
            # Ищем в meta
            csrf_meta = soup.find('meta', {'name': 'csrf-token'})
            if csrf_meta:
                csrf_token = csrf_meta.get('content')

        # 2. Подготовить данные для отправки
        # Обычно на странице есть форма с полем "text" и кнопкой "Проверить"
        # Эндпоинт для отправки — скорее всего /lab/neurodetector/upload или /check
        upload_url = 'https://yandex.ru/lab/neurodetector/check'  # предположительно
        data = {
            'text': text,
        }
        if csrf_token:
            data['csrf_token'] = csrf_token

        # 3. Отправить текст
        # Может быть как POST с multipart/form-data, так и application/x-www-form-urlencoded
        resp = session.post(upload_url, data=data, timeout=15)
        if resp.status_code != 200:
            # Попробуем другой эндпоинт
            upload_url = 'https://yandex.ru/lab/neurodetector/upload'
            resp = session.post(upload_url, data=data, timeout=15)
        resp.raise_for_status()

        # 4. Распарсить результат
        # Ожидается JSON-ответ с полями 'ai', 'likely_ai', 'likely_human', 'human'
        try:
            result_json = resp.json()
            # Пример: {"ai": 10, "likely_ai": 20, "likely_human": 30, "human": 40}
            if all(k in result_json for k in ['ai', 'likely_ai', 'likely_human', 'human']):
                return result_json
        except json.JSONDecodeError:
            # Возможно, ответ в HTML — тогда парсим HTML
            soup_result = BeautifulSoup(resp.text, 'html.parser')
            # Ищем элементы с классами для результатов
            # Примерные классы: 'neuro-ai', 'neuro-likely-ai', ...
            # Это нужно адаптировать под актуальную структуру
            # Возвращаем None, т.к. мы не знаем точных селекторов
            logger.warning("Yandex NeuroDetector returned HTML, cannot parse reliably.")
            return None

        # Если получили что-то другое, логируем
        logger.info(f"Yandex NeuroDetector response: {resp.text[:200]}")
        return None

    except Exception as e:
        logger.error(f"Yandex NeuroDetector parsing error: {e}")
        return None


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    logger.info("=== api_revise: START (paragraph-by-paragraph) ===")
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

        paragraphs = split_paragraphs(chapter_text)
        total = len(paragraphs)
        logger.info(f"Split into {total} paragraphs")

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем обработку {total} абзацев..."})

                results = []
                for idx, para in enumerate(paragraphs):
                    yield _sse("paragraph_start", {
                        "index": idx,
                        "original": para,
                        "status": "processing"
                    })

                    try:
                        result = process_paragraph(para, style)
                    except Exception as e:
                        logger.error(f"Paragraph {idx} processing error: {e}")
                        result = {
                            "original": para,
                            "revised": para,
                            "status": "error",
                            "chain": "none",
                            "human_score": 0
                        }
                    results.append(result)

                    yield _sse("paragraph_status", {
                        "index": idx,
                        "original": result["original"],
                        "revised": result["revised"],
                        "status": result["status"],
                        "chain": result["chain"],
                        "human_score": result.get("human_score", 0)
                    })

                    yield _sse("progress", {
                        "chars": idx + 1,
                        "estimated_total": total,
                        "percent": (idx + 1) / total * 100,
                        "log": f"Обработано {idx+1}/{total} абзацев"
                    })

                    yield _sse("paragraph_progress", {
                        "current": idx + 1,
                        "total": total,
                        "percent": (idx + 1) / total * 100
                    })

                final_text = "\n\n".join(r["revised"] for r in results)

                scores = [r.get("human_score", 0) for r in results if r.get("human_score", 0) > 0]
                avg_score = sum(scores) // len(scores) if scores else 0

                # Встроенный анализ
                overall = analyze_overall(final_text)

                # === ПАРСИНГ НЕЙРОДЕТЕКТОРА ЯНДЕКСА (опционально) ===
                yandex_result = None
                if ENABLE_YANDEX_PARSER and BS_AVAILABLE:
                    logger.info("Calling Yandex NeuroDetector parser...")
                    yandex_result = parse_yandex_neurodetector(final_text)
                    if yandex_result:
                        logger.info(f"Yandex result: {yandex_result}")
                    else:
                        logger.warning("Yandex NeuroDetector parsing failed.")

                # === ОТЛАДОЧНОЕ ЛОГИРОВАНИЕ ===
                logger.info("=== ОТЛАДОЧНОЕ ЛОГИРОВАНИЕ ТЕКСТОВ ===")
                logger.info(f"1. ИСХОДНЫЙ ТЕКСТ (длина: {len(chapter_text)}): {chapter_text[:500]}...")
                logger.info(f"2. ОТРЕДАКТИРОВАННЫЙ ТЕКСТ (длина: {len(final_text)}): {final_text[:500]}...")
                logger.info(f"3. ТЕКСТ ДЛЯ НЕЙРОДЕТЕКТОРА (длина: {len(final_text)}): {final_text[:500]}...")
                logger.info(f"Встроенный анализ: {overall}")
                if yandex_result:
                    logger.info(f"Яндекс-нейродетектор: {yandex_result}")
                else:
                    logger.info("Яндекс-нейродетектор: не получен")
                logger.info("=== КОНЕЦ ОТЛАДОЧНОГО ЛОГИРОВАНИЯ ===")

                yield _sse("done", {
                    "revised_text": final_text,
                    "original_text": chapter_text,
                    "summary": f"Обработано {total} абзацев. Успешно: {sum(1 for r in results if r['status']=='done')}, ошибок: {sum(1 for r in results if r['status']=='error')}. Средний HUMAN: {avg_score}%",
                    "paragraphs": results,
                    "average_human_score": avg_score,
                    "overall_analysis": overall,
                    "yandex_analysis": yandex_result,
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
