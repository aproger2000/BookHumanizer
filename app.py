"""
Chapter Editor v3.8.0 — локальное «очеловечивание» без цепочки переводов
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

APP_VERSION = "3.8.0"

MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# === НОВЫЙ МОДУЛЬ: локальное «очеловечивание» ===

class Humanizer:
    """Класс для локального улучшения естественности текста без потери длины."""
    
    def __init__(self):
        # Расширенный словарь синонимов (безопасные замены)
        self.synonyms = {
            r'\bсказал\b': ['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал', 'отозвался'],
            r'\bсказала\b': ['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала', 'отозвалась'],
            r'\bспросил\b': ['поинтересовался', 'осведомился', 'задал вопрос', 'полюбопытствовал'],
            r'\bспросила\b': ['поинтересовалась', 'осведомилась', 'задала вопрос', 'полюбопытствовала'],
            r'\bответил\b': ['откликнулся', 'парировал', 'возразил', 'подтвердил'],
            r'\bответила\b': ['откликнулась', 'парировала', 'возразила', 'подтвердила'],
            r'\bочень\b': ['весьма', 'крайне', 'чрезвычайно', 'невероятно'],
            r'\bхорошо\b': ['превосходно', 'отлично', 'замечательно', 'классно'],
            r'\bплохо\b': ['скверно', 'дурно', 'неважно', 'так себе'],
            r'\bбыстро\b': ['стремительно', 'мгновенно', 'рывком', 'в одно мгновение'],
            r'\bмедленно\b': ['неспешно', 'неторопливо', 'вяло', 'с ленцой'],
            r'\bбольшой\b': ['огромный', 'громадный', 'колоссальный', 'грандиозный'],
            r'\bмаленький\b': ['крошечный', 'миниатюрный', 'небольшой', 'малюсенький'],
            r'\bсмотреть\b': ['вглядываться', 'всматриваться', 'наблюдать', 'глазеть'],
            r'\bувидел\b': ['заметил', 'приметил', 'углядел', 'узрел'],
            r'\bпонял\b': ['осознал', 'сообразил', 'смекнул', 'догадался'],
            r'\bдумать\b': ['размышлять', 'соображать', 'прикидывать', 'считать'],
            r'\bзнать\b': ['ведать', 'понимать', 'осознавать', 'догадываться'],
            r'\bидти\b': ['шагать', 'двигаться', 'направляться', 'топать'],
            r'\bстоять\b': ['выситься', 'возвышаться', 'торчать', 'находиться'],
            r'\bсидеть\b': ['восседать', 'расположиться', 'устроиться', 'плюхнуться'],
            r'\bлежать\b': ['покоиться', 'валяться', 'возлежать', 'растянуться'],
            r'\bснова\b': ['опять', 'вновь', 'заново', 'сызнова'],
            r'\bтолько\b': ['лишь', 'едва', 'только что', 'всего лишь'],
            r'\bвдруг\b': ['неожиданно', 'внезапно', 'врасплох', 'как гром среди ясного неба'],
            r'\bконечно\b': ['разумеется', 'естественно', 'безусловно', 'ясное дело'],
            r'\bвозможно\b': ['вероятно', 'похоже', 'должно быть', 'наверное'],
            r'\bпоэтому\b': ['потому', 'оттого', 'следовательно', 'стало быть'],
        }
        
        # Вводные слова и частицы для оживления текста (без изменения смысла)
        self.insertions = {
            'start': ['Вот ', 'Значит, ', 'Итак, ', 'Так что, ', 'В общем, '],
            'middle': [' же', ' всё-таки', ' даже', ' прямо', ' как бы', ' почти', ' совсем'],
            'end': [', в общем', ', кстати', ', например', ', разумеется', ', между прочим'],
        }
        
        # Устойчивые обороты для замены
        self.phrase_replaces = {
            r'в конце концов': ['в итоге', 'в конечном счёте', 'в результате'],
            r'с самого начала': ['изначально', 'сразу же', 'с первых шагов'],
            r'в одно мгновение': ['мгновенно', 'вмиг', 'в один миг'],
            r'время от времени': ['иногда', 'изредка', 'временами'],
            r'так или иначе': ['в любом случае', 'как бы там ни было', 'так и сяк'],
        }

    def apply_synonymization(self, text: str) -> str:
        """Заменяет слова на синонимы, сохраняя длину."""
        # Проходим по всем словам, выбираем случайный синоним, если слово в словаре
        words = text.split(' ')
        new_words = []
        for word in words:
            # Убираем знаки препинания для поиска
            clean_word = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
            if clean_word.lower() in self.synonyms:
                # Выбираем случайный синоним, не меняя регистр
                syn = random.choice(self.synonyms[clean_word.lower()])
                # Сохраняем регистр первой буквы, если нужно
                if clean_word[0].isupper():
                    syn = syn.capitalize()
                # Восстанавливаем знаки препинания
                suffix = word[len(clean_word):]
                new_words.append(syn + suffix)
            else:
                new_words.append(word)
        return ' '.join(new_words)

    def apply_phrase_replacement(self, text: str) -> str:
        """Заменяет устойчивые фразы на синонимы."""
        for pattern, variants in self.phrase_replaces.items():
            replacements = random.sample(variants, 1)
            text = re.sub(pattern, replacements[0], text, flags=re.IGNORECASE)
        return text

    def apply_insertions(self, text: str) -> str:
        """Добавляет вводные слова в предложения (без изменения смысла)."""
        # Разбиваем на предложения
        sentences = re.split(r'(?<=[.!?])\s+', text)
        new_sentences = []
        for sent in sentences:
            if not sent.strip():
                continue
            # В начале предложения — вставка (с вероятностью 30%)
            if random.random() < 0.3:
                insertion = random.choice(self.insertions['start'])
                sent = insertion + sent[0].lower() + sent[1:]
            # В середине (после первого слова) — с вероятностью 20%
            if random.random() < 0.2 and len(sent.split()) > 4:
                words = sent.split()
                pos = random.randint(1, min(3, len(words)-1))
                insertion = random.choice(self.insertions['middle'])
                words.insert(pos, insertion)
                sent = ' '.join(words)
            new_sentences.append(sent)
        return '. '.join(new_sentences)

    def apply_dialog_variety(self, text: str) -> str:
        """Разнообразит диалоговые теги (только для прямых речей с тире)."""
        # Ищем диалоги вида: "— текст, — сказал он."
        pattern = r'(—[^—]+?—\s*)(\w+)(\s+[а-яА-Я]+[.,!?]?)'
        matches = re.finditer(pattern, text, re.DOTALL)
        # Собираем замены в обратном порядке
        replacements = []
        for match in matches:
            start, end = match.span()
            prefix = match.group(1)
            verb = match.group(2)
            suffix = match.group(3)
            # Заменяем глагол на синоним (если есть)
            if verb.lower() in self.synonyms:
                new_verb = random.choice(self.synonyms[verb.lower()])
                replacements.append((start, end, f"{prefix}{new_verb}{suffix}"))
        # Применяем замены (с конца, чтобы не сбивать индексы)
        for start, end, repl in reversed(replacements):
            text = text[:start] + repl + text[end:]
        return text

    def apply_word_order(self, text: str) -> str:
        """Меняет порядок слов в некоторых предложениях (вводные обороты)."""
        # Простая перестановка: перенос обстоятельства времени/места в начало
        sentences = re.split(r'(?<=[.!?])\s+', text)
        new_sentences = []
        for sent in sentences:
            if len(sent.split()) < 4:
                new_sentences.append(sent)
                continue
            # Ищем конструкции типа "он быстро пошёл" → "быстро он пошёл"
            words = sent.split()
            if len(words) >= 3 and words[1] in ['быстро', 'медленно', 'тихо', 'громко', 'вдруг']:
                # С вероятностью 30% переставляем
                if random.random() < 0.3:
                    words[0], words[1] = words[1], words[0]
                    sent = ' '.join(words)
            new_sentences.append(sent)
        return '. '.join(new_sentences)

    def humanize(self, text: str) -> str:
        """Полный цикл «очеловечивания»."""
        # Применяем все преобразования с контролем длины
        original_len = len(text)
        
        # 1. Замена устойчивых фраз
        text = self.apply_phrase_replacement(text)
        
        # 2. Синонимизация
        text = self.apply_synonymization(text)
        
        # 3. Добавление вводных слов
        text = self.apply_insertions(text)
        
        # 4. Разнообразие диалогов
        text = self.apply_dialog_variety(text)
        
        # 5. Перестановка слов
        text = self.apply_word_order(text)
        
        # 6. Чистка лишних пробелов
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s*([.,!?;:])\s*', r'\1 ', text)
        text = re.sub(r'\s+', ' ', text)
        
        # Контроль длины: если уменьшилась более чем на 10%, добавляем вводные слова ещё раз
        if len(text) < original_len * 0.9:
            # Добавляем вводные фразы в конец предложений
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            for sent in sentences:
                if len(sent) > 20 and random.random() < 0.4:
                    insertion = random.choice(self.insertions['end'])
                    # Вставляем перед последним словом
                    words = sent.split()
                    if len(words) > 2:
                        words.insert(-1, insertion)
                        sent = ' '.join(words)
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
        
        return text


# === Функции для разбиения и полировки (без изменений) ===

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

                # === НОВАЯ ОБРАБОТКА: локальное очеловечивание ===
                logger.info("Step 1: Local humanization...")
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
                logger.info(f"Final: {final_len} chars, {final_para_count} paragraphs")

                yield _sse("done", {
                    "revised_text": processed_text,
                    "original_text": chapter_text,
                    "summary": f"Текст обработан локально (синонимы, вводные слова, перестановки). Потеря длины: {(1 - final_len/len(chapter_text)):.1%}. Абзацев: {final_para_count}",
                    "changes": [
                        "Локальное «очеловечивание» без цепочек переводов",
                        "Замена слов на синонимы",
                        "Добавление вводных слов и частиц",
                        "Разнообразие диалоговых тегов",
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
