"""
Chapter Editor v3.6.2 — базовая версия + опциональный модуль перефразирования
(остальная часть кода — как в v3.6.2, изменения только в import и generate)
"""
import os
import logging
import random
import re
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

# Импортируем модуль humanizer (если он доступен)
try:
    import humanizer
    HUMANIZER_AVAILABLE = True
except ImportError:
    HUMANIZER_AVAILABLE = False
    logging.warning("humanizer module not found. Humanizer disabled.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "3.6.2"
MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Проверяем переменную окружения для включения humanizer
ENABLE_HUMANIZER = os.environ.get("ENABLE_HUMANIZER", "false").lower() == "true"
if ENABLE_HUMANIZER and not HUMANIZER_AVAILABLE:
    logger.warning("ENABLE_HUMANIZER is true but humanizer module not available.")
    ENABLE_HUMANIZER = False

# ... (все остальные функции: translate_with_fallback, process_chunk_through_chain, 
#      split_text_into_chunks, apply_translation_chain_full, clean_translation_artifacts,
#      split_into_paragraphs_by_logic, apply_light_polish — без изменений, как в v3.6.2)

# В функции generate после финальной полировки:
        # 5. Финальная полировка
        logger.info("Step 5: Final polish...")
        processed_text = apply_light_polish(processed_text)

        # 6. Опциональное перефразирование (humanizer)
        if ENABLE_HUMANIZER and HUMANIZER_AVAILABLE and len(processed_text) > 200:
            logger.info("Step 6: Applying humanizer (local paraphrasing)...")
            processed_text = humanizer.enhance_text(processed_text, probability=0.25)
            yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 95, "log": "Перефразирование выполнено"})

        yield _sse("progress", {"chars": len(processed_text), "estimated_total": original_len, "percent": 100, "log": "Готово!"})
        final_len = len(processed_text)
        # ... остальное без изменений
