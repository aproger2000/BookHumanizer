"""
Chapter Editor v4.3.0 — с калиброванным HUMAN-детектором
"""
import json
import os
import re
import logging
import random
import joblib
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

random.seed(42)

# ========== Загрузка модели ==========
MODEL_LOADED = False
human_model = None
feature_cols = []

try:
    human_model = joblib.load('human_model.pkl')
    with open('feature_cols.txt', 'r') as f:
        feature_cols = [col.strip() for col in f.read().strip().split(',') if col.strip()]
    MODEL_LOADED = True
    logger.info("Модель HUMAN загружена успешно.")
except Exception as e:
    logger.warning(f"Не удалось загрузить модель: {e}. Будет использована эвристика.")

# ========== Словарь синонимов (для пост-обработки) ==========
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


def extract_features(text: str) -> dict:
    """Извлекает признаки для модели (те же, что использовались при обучении)."""
    features = {}
    letters = sum(1 for ch in text if ch.isalpha())
    if letters == 0:
        features['latin_ratio'] = 0.0
    else:
        latin_count = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
        features['latin_ratio'] = latin_count / letters

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
    marker_count = 0
    for m in markers:
        if re.search(m, text, flags=re.IGNORECASE):
            marker_count += 1
    features['marker_count'] = marker_count

    words = re.findall(r'[а-яА-Яa-zA-Z]+', text)
    if words:
        features['avg_word_len'] = sum(len(w) for w in words) / len(words)
    else:
        features['avg_word_len'] = 0

    word_counts = {}
    for w in words:
        w_lower = w.lower()
        word_counts[w_lower] = word_counts.get(w_lower, 0) + 1
    features['max_repeat_ratio'] = max(word_counts.values()) / len(words) if words else 0

    sentences = re.split(r'(?<=[.!?])\s+', text)
    features['num_sentences'] = len(sentences)
    if sentences:
        sent_word_counts = [len(s.split()) for s in sentences]
        features['avg_sentence_len'] = sum(sent_word_counts) / len(sent_word_counts)
    else:
        features['avg_sentence_len'] = 0

    dialog_count = sum(1 for s in sentences if re.match(r'^[—"«]', s.strip()))
    features['dialog_ratio'] = dialog_count / len(sentences) if sentences else 0

    features['question_marks'] = text.count('?')
    features['exclamation_marks'] = text.count('!')
    unique_words = set(w.lower() for w in words)
    features['lexical_diversity'] = len(unique_words) / len(words) if words else 0

    return features


def get_human_score(text: str) -> int:
    """
    Использует обученную модель для предсказания HUMAN (в процентах).
    Если модель не загружена, использует эвристический fallback.
    """
    if not text or len(text) < 20:
        return 50

    # Если модель загружена – используем её
    if MODEL_LOADED and human_model is not None and feature_cols:
        try:
            features = extract_features(text)
            X = [[features.get(col, 0) for col in feature_cols]]
            pred = human_model.predict(X)[0]
            return max(0, min(100, int(round(pred))))
        except Exception as e:
            logger.warning(f"Ошибка при предсказании модели: {e}. Использую эвристику.")
            # fallback на эвристику

    # ======== Эвристический fallback (старый код) ========
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
    """Пост-обработка (синонимы, вставки, перестановки) — без изменений."""
    if not text or len(text) < 20:
        return text
    if logs is None:
        logs = []

    # Синонимы (30%)
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

    # Вставки (15%)
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

    # Перестановки (10%)
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


# ========== Остальные функции без изменений ==========
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
        "chain": "LOCAL (калиброванный)",
        "human_score": score,
        "logs": logs
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


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    # ... (код без изменений, как в v4.2.1)
    # Если вы не хотите копировать весь огромный код, можете оставить старую функцию api_revise,
    # но с обновлённым get_human_score.
    pass


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
