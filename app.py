"""
Chapter Editor v4.8.0 — с автоматическим дообучением модели
"""
import json
import os
import sys
import re
import logging
import random
import joblib
import subprocess
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

# Импортируем настройки из config.py
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.8.0"
MAX_CHARS = 30_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

random.seed(config.RANDOM_SEED)

# ========== Загрузка калибровочной модели ==========
MODEL_LOADED = False
human_model = None
feature_cols = []

def load_model():
    global human_model, feature_cols, MODEL_LOADED
    try:
        human_model = joblib.load('human_model.pkl')
        with open('feature_cols.txt', 'r') as f:
            feature_cols = [col.strip() for col in f.read().strip().split(',') if col.strip()]
        MODEL_LOADED = True
        logger.info("Калибровочная модель HUMAN загружена.")
    except Exception as e:
        MODEL_LOADED = False
        logger.warning(f"Не удалось загрузить калибровочную модель: {e}")

load_model()

# ========== Инициализация ruT5 (отключена) ==========
RU_T5_AVAILABLE = False
ru_model = None
ru_tokenizer = None

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
    # (функция без изменений, уже была)
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
    # эвристический fallback (старый код, можно оставить или убрать)
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

# ========== Пост-обработка (без изменений, как в v1.7) ==========
# Здесь вставьте полную функцию post_process из стабильной версии (v1.7)
# Я привожу её сокращённо, но в реальном файле она должна быть полной.

def post_process(text: str, logs: list = None) -> str:
    if not text or len(text) < 20:
        return text
    if logs is None:
        logs = []

    ops = []
    if random.random() < config.PROB_SYNONYMS:
        ops.append('synonyms')
    if random.random() < config.PROB_INSERTIONS:
        ops.append('insertions')
    if random.random() < config.PROB_SWAP_FIRST_WORDS:
        ops.append('swap_first_words')
    if random.random() < config.PROB_INTERJECTIONS:
        ops.append('interjections')
    if random.random() < config.PROB_PARTICLES:
        ops.append('insert_particles')
    if random.random() < config.PROB_CANCEL_CANCEL:
        ops.append('cancel_cancel')
    if random.random() < config.PROB_REMOVE_AI_MARKERS:
        ops.append('remove_ai_markers')
    if random.random() < config.PROB_SPLIT_LONG_SENTENCES:
        ops.append('split_long_sentences')
    if random.random() < config.PROB_ADD_COLLOQUIAL:
        ops.append('add_colloquial')
    if random.random() < config.PROB_TYPOS:
        ops.append('add_typos')
    # Отключённые операции
    # if random.random() < config.PROB_SWAP_CLAUSES: ops.append('swap_clauses')
    # ...

    if not ops:
        ops.append('synonyms')

    for op in ops:
        # Здесь все блоки операций из вашего стабильного app.py (v1.7)
        # Для краткости я не дублирую их полностью, но в вашем файле они должны быть.
        pass
    return text

# ========== Обработка абзаца ==========
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
    original_score = get_human_score(paragraph)
    logs.append(f"Оригинальный HUMAN: {original_score}%")

    if original_score >= 50:
        return {
            "original": paragraph,
            "revised": paragraph,
            "status": "done",
            "chain": "LOCAL (skipped)",
            "human_score": original_score,
            "logs": logs + ["Абзац уже имеет HUMAN >= 50, пропущен"]
        }

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
    ...

# ========== Flask endpoints ==========
@app.get("/api/health")
def health():
    return jsonify(
        status="ok",
        version=APP_VERSION,
        config_version=config.CONFIG_VERSION,
        hypothesis=config.HYPOTHESIS
    )

RETRAIN_TOKEN = os.environ.get("RETRAIN_TOKEN", "your-secret-token")

@app.post("/api/retrain")
def api_retrain():
    token = request.headers.get("X-Retrain-Token")
    if token != RETRAIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        result = subprocess.run(
            [sys.executable, "retrain_model.py"],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            return jsonify({"error": "Retrain failed", "details": result.stderr}), 500
        # Перезагружаем модель
        load_model()
        return jsonify({"status": "ok", "output": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/reload_model")
def reload_model():
    try:
        load_model()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/revise")
def api_revise():
    # (полная функция из предыдущих версий)
    # Для краткости оставлю заглушку, но в реальном файле она должна быть полной.
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
