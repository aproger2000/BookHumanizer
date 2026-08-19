"""
Chapter Editor v3.0.0 — Humanization via Translation Chain.
Uses DeepSeek for LLM rewriting and Google Translate for structural disruption.
"""
import io
import json
import os
import re
import time
import logging
import asyncio
import requests
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException
from googletrans import Translator  # Установите: pip install googletrans==4.0.0-rc1

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.0.0"

# Конфигурация API
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
ANTHROPIC_API_URL = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")

# Ключи API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

MAX_CHARS = 10_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

class ChapterEditError(RuntimeError):
    pass

def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

# --- Основная логика обработки ---

def rewrite_with_llm(text: str, style_hint: str = "", temperature: float = 1.3) -> str:
    """Переписывает текст через LLM (DeepSeek), переводя на другой язык."""
    if not DEEPSEEK_API_KEY:
        raise ChapterEditError("DEEPSEEK_API_KEY не установлен")

    # Инструкция для LLM: переписать и перевести на целевой язык
    system_prompt = f"You are a professional editor. Rewrite the following text in Chinese, making it sound natural, varied, and human. Preserve all facts and plot. {style_hint}"
    user_prompt = f"Rewrite this text in Chinese:\n\n{text}"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature,
        "max_tokens": 4000,
        "stream": False
    }

    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        raise ChapterEditError(f"DeepSeek rewrite failed: {e}")

def translate_text(text: str, src_lang: str, dest_lang: str) -> str:
    """Переводит текст через Google Translate (публичный API)."""
    try:
        # Используем googletrans для простоты, но можно заменить на официальный API
        translator = Translator()
        # googletrans требует asyncio, обёртка для синхронного вызова
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(translator.translate(text, src=src_lang, dest=dest_lang))
        loop.close()
        return result.text
    except Exception as e:
        logger.error(f"Google Translate error: {e}")
        # Если переводчик упал, возвращаем исходный текст
        return text

def apply_translation_chain(text: str) -> str:
    """Основной метод: Translation Chain из репозитория Humanize-Text."""
    logger.info("Starting translation chain...")
    
    # Шаг 1: LLM переписывает текст на китайском
    zh_text = rewrite_with_llm(text, "The output must be in Chinese.")
    logger.info(f"Step 1 (EN->ZH) complete. Length: {len(zh_text)}")
    
    # Шаг 2: LLM переписывает китайский текст на японском
    ja_text = rewrite_with_llm(zh_text, "The output must be in Japanese. Use the previous text as context.")
    logger.info(f"Step 2 (ZH->JA) complete. Length: {len(ja_text)}")
    
    # Шаг 3: Машинный перевод (Японский -> Финский)
    fi_text = translate_text(ja_text, src_lang='ja', dest_lang='fi')
    logger.info(f"Step 3 (JA->FI) complete. Length: {len(fi_text)}")
    
    # Шаг 4: Машинный перевод (Финский -> Английский)
    final_text = translate_text(fi_text, src_lang='fi', dest_lang='en')
    logger.info(f"Step 4 (FI->EN) complete. Length: {len(final_text)}")
    
    return final_text

def final_polish_with_claude(text: str, style_hint: str = "") -> str:
    """Финальная правка от Claude (опционально, для стиля)."""
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set, skipping Claude polish.")
        return text

    logger.info("Applying final Claude polish...")
    system_prompt = f"You are a literary editor. Lightly polish the following English text for natural flow and a {style_hint or 'neutral'} style. Do not change facts or plot."
    user_prompt = f"Polish this text:\n\n{text}"

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-5",
        "max_tokens": 4000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "stream": False
    }

    try:
        response = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        return result['content'][0]['text'].strip()
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return text  # Возвращаем текст без правки

# --- Flask маршруты ---

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

        # Получаем текст
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

        style_hint = STYLE_PRESETS.get(style, "")

        def generate():
            try:
                # Шаг 1: Отправляем прогресс
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0})

                # Основная обработка
                logger.info("Applying translation chain...")
                processed_text = apply_translation_chain(chapter_text)

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 80})

                # Опционально: финальная полировка Claude
                if ANTHROPIC_API_KEY:
                    logger.info("Applying Claude polish...")
                    processed_text = final_polish_with_claude(processed_text, style_hint)

                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100})

                # Возвращаем результат
                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": "Текст переработан методом переводной цепочки.",
                    "changes": ["Переписан через DeepSeek на китайском и японском", "Переведён через Google Translate на финский и обратно"],
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

# --- STYLE PRESETS (для совместимости) ---
STYLE_PRESETS = {
    "neutral": "",
    "dynamic_scifi": "fast-paced, cinematic, with short, punchy sentences.",
}

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
