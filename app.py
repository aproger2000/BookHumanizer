"""
Chapter Editor v4.3.0 — стабильный baseline с параметрами v4.0.1
"""
import json
import os
import re
import logging
import random
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.3.0"
MAX_CHARS = 30_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Фиксируем seed для воспроизводимости
random.seed(42)

# Словарь синонимов (базовый, как в v4.0.1)
SYNONYMS = {
    r'\bсказал\b': ['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал', 'отозвался'],
    r'\bсказала\b': ['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала', 'отозвалась'],
    r'\bспросил\b': ['поинтересовался', 'осведомился', 'полюбопытствовал', 'задал вопрос'],
    r'\bспросила\b': ['поинтересовалась', 'осведомилась', 'полюбопытствовала', 'задала вопрос'],
    r'\bответил\b': ['откликнулся', 'парировал', 'возразил', 'подтвердил'],
    r'\bответила\b': ['откликнулась', 'парировала', 'возразила', 'подтвердила'],
    r'\bочень\b': ['весьма', 'крайне', 'чрезвычайно', 'невероятно'],
    r'\bхорошо\b': ['превосходно', 'отлично', 'замечательно', 'классно'],
    r'\bплохо\b': ['скверно', 'неважно', 'так себе'],
    r'\bбыстро\b': ['стремительно', 'мгновенно', 'рывком'],
    r'\bмедленно\b': ['неспешно', 'неторопливо', 'вяло'],
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
    r'\bтолько\b': ['лишь', 'едва', 'всего лишь', 'только что'],
    r'\bвдруг\b': ['неожиданно', 'внезапно', 'врасплох', 'как гром среди ясного неба'],
    r'\bконечно\b': ['разумеется', 'естественно', 'безусловно', 'ясное дело'],
    r'\bвозможно\b': ['вероятно', 'похоже', 'должно быть', 'наверное'],
    r'\bпоэтому\b': ['потому', 'оттого', 'следовательно', 'стало быть'],
    r'\bпросто\b': ['всего-навсего', 'элементарно', 'банально'],
    r'\bсовсем\b': ['вовсе', 'абсолютно', 'совершенно'],
    r'\bпочти\b': ['едва ли не', 'практически', 'без малого'],
}

INSERTIONS = ['впрочем', 'кстати', 'разумеется', 'пожалуй', 'кажется', 'несомненно', 'в общем']

def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def get_human_score(text: str) -> int:
    # Ваш текущий эвристический детектор (можно пока оставить)
    # В будущем его можно заменить на калиброванную модель
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


def post_process(text: str, logs: list = None) -> str:
    """
    Параметры v4.0.1:
    - синонимы: 30%
    - вставки: 15%
    - перестановки: 10%
    """
    if not text or len(text) < 20:
        return text
    if logs is None:
        logs = []

    # 1. Синонимы (30%)
    words = text.split(' ')
    new_words = []
    replacements = 0
    for word in words:
        clean = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
        if clean.lower() in SYNONYMS and random.random() < 0.3:
            syn = random.choice(SYNONYMS[clean.lower()])
            if clean[0].isupper():
                syn = syn.capitalize()
            suffix = word[len(clean):]
            new_words.append(syn + suffix)
            replacements += 1
        else:
            new_words.append(word)
    text = ' '.join(new_words)
    if replacements:
        logs.append(f"  - заменено синонимов: {replacements}")

    # 2. Вставки (15%)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    new_sentences = []
    inserted = 0
    for sent in sentences:
        if len(sent.split()) > 5 and random.random() < 0.15:
            words = sent.split()
            pos = random.randint(1, min(3, len(words)-1))
            ins = random.choice(INSERTIONS)
            words.insert(pos, ins + ',')
            sent = ' '.join(words)
            inserted += 1
        new_sentences.append(sent)
    text = '. '.join(new_sentences)
    if inserted:
        logs.append(f"  - вставлено вводных слов: {inserted}")

    # 3. Перестановки (10%)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    new_sentences = []
    swapped = 0
    for sent in sentences:
        if len(sent.split()) > 4 and random.random() < 0.1:
            words = sent.split()
            if len(words) >= 3 and not words[0].startswith(('—', '"', '«')):
                words[0], words[1] = words[1], words[0]
                sent = ' '.join(words)
                swapped += 1
        new_sentences.append(sent)
    text = '. '.join(new_sentences)
    if swapped:
        logs.append(f"  - перестановок первых слов: {swapped}")

    return text


def split_paragraphs(text: str) -> list:
    if not text:
        return []
    text = text.replace('\r\n', '\n')
    paragraphs = text.split('\n\n')
    return [p.strip() for p in paragraphs if p.strip()]


def process_paragraph(paragraph: str) -> dict:
    if not paragraph:
        return {
            "original": paragraph,
            "revised": paragraph,
            "status": "error",
            "chain": "LOCAL",
            "human_score": 0,
            "logs": ["Пустой абзац"]
        }
    logs = []
    revised = post_process(paragraph, logs=logs)
    score = get_human_score(revised)
    logs.append(f"Итоговый HUMAN score: {score}%")
    return {
        "original": paragraph,
        "revised": revised,
        "status": "done" if score > 50 else "partial",
        "chain": "LOCAL (v4.3.0)",
        "human_score": score,
        "logs": logs
    }


def analyze_overall(text: str) -> dict:
    # без изменений
    ...


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    # без изменений
    ...


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
