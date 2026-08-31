"""
Chapter Editor v4.7.0 — с выборочным перефразированием через ruT5-tiny
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

# Импортируем настройки из config.py
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.7.0"
MAX_CHARS = 30_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

random.seed(config.RANDOM_SEED)

# ========== Загрузка калибровочной модели ==========
MODEL_LOADED = False
human_model = None
feature_cols = []

try:
    human_model = joblib.load('human_model.pkl')
    with open('feature_cols.txt', 'r') as f:
        feature_cols = [col.strip() for col in f.read().strip().split(',') if col.strip()]
    MODEL_LOADED = True
    logger.info("Калибровочная модель HUMAN загружена.")
except Exception as e:
    logger.warning(f"Не удалось загрузить калибровочную модель: {e}")

# ========== Инициализация ruT5 ==========
RU_T5_AVAILABLE = False
ru_model = None
ru_tokenizer = None

if config.USE_RU_T5:
    try:
        from transformers import T5ForConditionalGeneration, T5Tokenizer
        import torch
        TRANSFORMERS_AVAILABLE = True
    except ImportError:
        TRANSFORMERS_AVAILABLE = False
        logger.warning("transformers/torch не установлены, ruT5 недоступен.")

    if TRANSFORMERS_AVAILABLE:
        try:
            model_name = "cointegrated/ruT5-tiny"
            logger.info(f"Загрузка {model_name}...")
            ru_tokenizer = T5Tokenizer.from_pretrained(model_name)
            ru_model = T5ForConditionalGeneration.from_pretrained(
                model_name,
                torch_dtype=torch.float16,      # экономия памяти
                low_cpu_mem_usage=True
            )
            ru_model.eval()
            RU_T5_AVAILABLE = True
            logger.info("ruT5-tiny загружена успешно.")
        except Exception as e:
            logger.warning(f"Не удалось загрузить ruT5: {e}")
else:
    logger.info("ruT5 отключён в конфиге.")

# ========== Используем словари из config ==========
SYNONYMS = config.SYNONYMS_DICT
INSERTIONS = config.INSERTIONS_LIST
INTERJECTIONS = config.INTERJECTIONS_LIST
PARTICLES = config.PARTICLES_LIST
ADVERBS = config.ADVERBS_LIST
REPORTING_VERBS = config.REPORTING_VERBS
CLAUSE_CONJUNCTIONS = config.CLAUSE_CONJUNCTIONS

CANCEL_CANCEL_DICT = config.CANCEL_CANCEL_DICT
AI_MARKERS = config.AI_MARKERS
COLLOQUIAL_PARTICLES = config.COLLOQUIAL_PARTICLES

# ========== Вспомогательные функции ==========
def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def extract_features(text: str) -> dict:
    # (без изменений, та же функция, что была)
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
    if not text or len(text) < 20:
        return 50
    if MODEL_LOADED and human_model is not None and feature_cols:
        try:
            features = extract_features(text)
            X = [[features.get(col, 0) for col in feature_cols]]
            pred = human_model.predict(X)[0]
            return max(0, min(100, int(round(pred))))
        except Exception as e:
            logger.warning(f"Ошибка предсказания: {e}. Использую эвристику.")
    # эвристический fallback (старый код)
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
    # (полная функция из предыдущей версии — без изменений)
    # Для краткости я не дублирую её здесь, но она должна быть полностью скопирована из v4.6.1
    # Она включает все операции: синонимы, вставки, перестановки, инверсии, опечатки и т.д.
    pass


def rewrite_with_ru_t5(text: str, logs: list = None) -> str:
    """
    Перефразирует текст с помощью ruT5-tiny.
    Возвращает лучший вариант из нескольких попыток.
    """
    if not RU_T5_AVAILABLE:
        return text

    if not text or len(text) < config.MIN_PARAGRAPH_LENGTH:
        return text

    best_text = text
    best_score = 0

    for attempt in range(config.RU_T5_ATTEMPTS):
        try:
            input_text = f"paraphrase: {text}"
            inputs = ru_tokenizer(input_text, return_tensors="pt", truncation=True, max_length=256)

            with torch.no_grad():
                outputs = ru_model.generate(
                    **inputs,
                    max_length=256,
                    temperature=config.RU_T5_TEMPERATURE,
                    do_sample=True,
                    top_p=0.95,
                    repetition_penalty=1.2,
                    num_beams=1
                )
            paraphrased = ru_tokenizer.decode(outputs[0], skip_special_tokens=True)

            if paraphrased and len(paraphrased) > 5:
                # После модели можно применить лёгкую пост-обработку
                post_logs = []
                paraphrased = post_process(paraphrased, logs=post_logs)
                score = get_human_score(paraphrased)
                if logs is not None:
                    logs.append(f"Попытка {attempt+1}: '{paraphrased[:50]}...' (HUMAN={score}%)")
                    logs.extend([f"  {l}" for l in post_logs])
                if score > best_score:
                    best_score = score
                    best_text = paraphrased
        except Exception as e:
            logger.warning(f"ruT5 попытка {attempt+1} ошибка: {e}")
            continue

    return best_text


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
    # Сначала оцениваем оригинал
    original_score = get_human_score(paragraph)
    logs.append(f"Оригинальный HUMAN: {original_score}%")

    # Если абзац уже хороший (>50) — оставляем как есть
    if original_score >= 50:
        return {
            "original": paragraph,
            "revised": paragraph,
            "status": "done",
            "chain": "LOCAL (skipped)",
            "human_score": original_score,
            "logs": logs + ["Абзац уже имеет HUMAN >= 50, пропущен"]
        }

    # Пробуем модель, если абзац достаточно длинный и включена
    if config.USE_RU_T5 and RU_T5_AVAILABLE:
        model_logs = []
        model_text = rewrite_with_ru_t5(paragraph, logs=model_logs)
        if model_text != paragraph:
            model_score = get_human_score(model_text)
            logs.extend(model_logs)
            logs.append(f"После ruT5 HUMAN: {model_score}%")
            # Если модель улучшила результат — берём её вариант
            if model_score > original_score:
                # Применяем лёгкую пост-обработку к результату модели
                final_logs = []
                final_text = post_process(model_text, logs=final_logs)
                final_score = get_human_score(final_text)
                logs.extend(final_logs)
                logs.append(f"Итоговый HUMAN: {final_score}%")
                return {
                    "original": paragraph,
                    "revised": final_text,
                    "status": "done" if final_score > 50 else "partial",
                    "chain": "LOCAL (ruT5 + post)",
                    "human_score": final_score,
                    "logs": logs
                }

    # Если модель не помогла или недоступна — применяем только пост-обработку
    post_logs = []
    revised = post_process(paragraph, logs=post_logs)
    score = get_human_score(revised)
    logs.extend(post_logs)
    logs.append(f"Итоговый HUMAN: {score}%")

    return {
        "original": paragraph,
        "revised": revised,
        "status": "done" if score > 50 else "partial",
        "chain": "LOCAL (post only)",
        "human_score": score,
        "logs": logs
    }


def analyze_overall(text: str) -> dict:
    # (без изменений)
    pass


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    # (без изменений, полная функция из предыдущих версий)
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
    return jsonify(detail=f"Server error: {str(e)}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
