"""
Chapter Editor v3.8.1 — локальное «очеловечивание» (только синонимы и диалоговые теги, без агрессивных вставок)
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

APP_VERSION = "3.8.1"

MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class Humanizer:
    """Локальное «очеловечивание»: безопасные замены без нарушения структуры."""

    def __init__(self):
        # Словарь синонимов (выбираем случайный синоним для слова)
        self.synonyms = {
            r'\bсказал\b': ['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал', 'отозвался'],
            r'\bсказала\b': ['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала', 'отозвалась'],
            r'\bспросил\b': ['поинтересовался', 'осведомился', 'полюбопытствовал'],
            r'\bспросила\b': ['поинтересовалась', 'осведомилась', 'полюбопытствовала'],
            r'\bответил\b': ['откликнулся', 'парировал', 'возразил', 'подтвердил'],
            r'\bответила\b': ['откликнулась', 'парировала', 'возразила', 'подтвердила'],
            r'\bочень\b': ['весьма', 'крайне', 'чрезвычайно'],
            r'\bхорошо\b': ['превосходно', 'отлично', 'замечательно'],
            r'\bплохо\b': ['скверно', 'неважно'],
            r'\bбыстро\b': ['стремительно', 'мгновенно'],
            r'\bмедленно\b': ['неспешно', 'неторопливо'],
            r'\bбольшой\b': ['огромный', 'громадный', 'колоссальный'],
            r'\bмаленький\b': ['крошечный', 'миниатюрный', 'небольшой'],
            r'\bсмотреть\b': ['вглядываться', 'всматриваться', 'наблюдать'],
            r'\bувидел\b': ['заметил', 'приметил', 'углядел'],
            r'\bпонял\b': ['осознал', 'сообразил', 'смекнул'],
            r'\bдумать\b': ['размышлять', 'соображать', 'прикидывать'],
            r'\bзнать\b': ['ведать', 'понимать', 'осознавать'],
            r'\bидти\b': ['шагать', 'двигаться', 'направляться'],
            r'\bстоять\b': ['выситься', 'возвышаться', 'находиться'],
            r'\bсидеть\b': ['восседать', 'расположиться', 'устроиться'],
            r'\bлежать\b': ['покоиться', 'валяться', 'возлежать'],
            r'\bснова\b': ['опять', 'вновь', 'заново'],
            r'\bтолько\b': ['лишь', 'едва', 'всего лишь'],
            r'\bвдруг\b': ['неожиданно', 'внезапно', 'врасплох'],
            r'\bконечно\b': ['разумеется', 'естественно', 'безусловно'],
            r'\bвозможно\b': ['вероятно', 'похоже', 'должно быть'],
            r'\bпоэтому\b': ['потому', 'оттого', 'следовательно'],
        }

        # Замена устойчивых фраз (редко)
        self.phrase_replaces = {
            r'в конце концов': ['в итоге', 'в конечном счёте'],
            r'с самого начала': ['изначально', 'сразу же'],
            r'в одно мгновение': ['мгновенно', 'вмиг'],
            r'время от времени': ['иногда', 'изредка'],
            r'так или иначе': ['в любом случае', 'как бы там ни было'],
            r'как правило': ['обычно', 'чаще всего'],
            r'в целом': ['в общем', 'в основном'],
        }

    def apply_synonymization(self, text: str) -> str:
        """Заменяет слова на синонимы, сохраняя длину."""
        words = text.split(' ')
        new_words = []
        for word in words:
            # Убираем знаки препинания для поиска
            clean_word = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
            if clean_word.lower() in self.synonyms:
                # С вероятностью 40% заменяем
                if random.random() < 0.4:
                    syn = random.choice(self.synonyms[clean_word.lower()])
                    # Сохраняем регистр первой буквы
                    if clean_word[0].isupper():
                        syn = syn.capitalize()
                    # Восстанавливаем знаки препинания
                    suffix = word[len(clean_word):]
                    new_words.append(syn + suffix)
                    continue
            new_words.append(word)
        return ' '.join(new_words)

    def apply_phrase_replacement(self, text: str) -> str:
        """Заменяет устойчивые фразы с вероятностью 30%."""
        for pattern, variants in self.phrase_replaces.items():
            if random.random() < 0.3:
                replacement = random.choice(variants)
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def apply_dialog_variety(self, text: str) -> str:
        """Разнообразит диалоговые теги (только для прямых речей с тире)."""
        # Ищем диалоги вида: "— текст, — сказал он." и заменяем глагол
        pattern = r'(—[^—]+?—\s*)(\w+)(\s+[а-яА-Я]+[.,!?]?)'
        matches = list(re.finditer(pattern, text, re.DOTALL))
        if not matches:
            return text
        # Собираем замены в обратном порядке
        replacements = []
        for match in matches:
            prefix = match.group(1)
            verb = match.group(2)
            suffix = match.group(3)
            if verb.lower() in self.synonyms:
                new_verb = random.choice(self.synonyms[verb.lower()])
                replacements.append((match.start(), match.end(), f"{prefix}{new_verb}{suffix}"))
        # Применяем замены с конца
        for start, end, repl in reversed(replacements):
            text = text[:start] + repl + text[end:]
        return text

    def humanize(self, text: str) -> str:
        """Полный цикл «очеловечивания»."""
        original_len = len(text)

        # 1. Замена устойчивых фраз (редко)
        text = self.apply_phrase_replacement(text)

        # 2. Синонимизация
        text = self.apply_synonymization(text)

        # 3. Разнообразие диалогов
        text = self.apply_dialog_variety(text)

        # 4. Чистка лишних пробелов (без добавления точек)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
        text = re.sub(r'\s+', ' ', text)

        # 5. Контроль длины: если уменьшилась более чем на 8%, добавляем случайные синонимы ещё раз
        if len(text) < original_len * 0.92:
            # Повторная синонимизация некоторых слов (безопасно)
            text = self.apply_synonymization(text)

        return text.strip()


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

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": len(chapter_text), "percent": 0, "log": "Начинаем обработку..."})

                # 1. Локальное очеловечивание (без агрессивных вставок)
                logger.info("Step 1: Local humanization (safe replacements)...")
                humanizer = Humanizer()
                processed_text = humanizer.humanize(chapter_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 50, "log": "Очеловечивание выполнено"})

                # 2. Разбиение на абзацы
                logger.info("Step 2: Paragraph splitting...")
                processed_text = split_into_paragraphs_by_logic(processed_text)
                para_count = len(processed_text.split('\n\n'))
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 80, "log": f"Абзацев: {para_count}"})

                # 3. Финальная полировка
                logger.info("Step 3: Final polish...")
                processed_text = apply_light_polish(processed_text)
                yield _sse("progress", {"chars": len(processed_text), "estimated_total": len(chapter_text), "percent": 100, "log": "Готово!"})

                final_len = len(processed_text)
                final_para_count = len(processed_text.split('\n\n'))
                logger.info(f"Final: {final_len} chars, {final_para_count} paragraphs, loss: {(1 - final_len/len(chapter_text)):.2%}")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст обработан локально (синонимы, диалоговые теги). Изменение длины: {(final_len/len(chapter_text)-1):.1%}. Абзацев: {final_para_count}",
                    "changes": [
                        "Локальное «очеловечивание» (синонимы, разнообразие диалогов)",
                        "Без внешних API и агрессивных вставок",
                        f"Разделён на {final_para_count} абзацев"
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
