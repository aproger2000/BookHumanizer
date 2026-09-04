"""
Chapter Editor v4.8.3 — форсированное переобучение после каждой оценки
"""
import json
import os
import sys
import re
import logging
import random
import joblib
import csv
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

import config
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "4.8.3"
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
        logger.info(f"Калибровочная модель HUMAN загружена. Признаков: {len(feature_cols)}")
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

# ========== Счётчик для фонового переобучения ==========
_retrain_lock = threading.Lock()

# ========== Функция переобучения (синхронная) ==========
def retrain_model_sync():
    """Переобучает модель на всех данных из training_data.csv и перезагружает её."""
    with _retrain_lock:
        logger.info("=== RETRAIN START (sync) ===")
        csv_path = Path('training_data.csv')
        if not csv_path.exists():
            logger.warning("training_data.csv не найден, переобучение пропущено.")
            return

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            logger.error(f"Ошибка чтения training_data.csv: {e}")
            return

        df = df.dropna(subset=['HUMAN_yandex'])
        if len(df) == 0:
            logger.info("Нет данных для обучения (0 строк).")
            return

        logger.info(f"Найдено {len(df)} записей.")

        # Извлекаем признаки
        feature_rows = []
        for idx, row in df.iterrows():
            text = row['processed_text']
            if not isinstance(text, str) or len(text) < 10:
                continue
            try:
                feats = extract_features(text)
                feature_rows.append(feats)
            except Exception as e:
                logger.warning(f"Ошибка извлечения признаков для строки {idx}: {e}")
                continue

        if not feature_rows:
            logger.warning("Не удалось извлечь признаки ни для одной записи.")
            return

        feature_names = list(feature_rows[0].keys())
        X = pd.DataFrame(feature_rows)[feature_names]
        y = df['HUMAN_yandex'].values[:len(X)]

        if len(X) < 3:
            logger.info(f"Слишком мало примеров ({len(X)}), переобучение пропущено.")
            return

        logger.info(f"Обучение на {len(X)} примерах...")
        model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
        model.fit(X, y)

        y_pred = model.predict(X)
        mae = mean_absolute_error(y, y_pred)
        logger.info(f"MAE на обучении: {mae:.2f}")

        # Сохраняем модель и список признаков
        joblib.dump(model, 'human_model.pkl')
        with open('feature_cols.txt', 'w') as f:
            f.write(','.join(feature_names))

        # Перезагружаем модель в памяти
        load_model()
        logger.info(f"Модель успешно переобучена и перезагружена. Всего строк: {len(df)}")
        logger.info("=== RETRAIN END ===")

# ========== Вспомогательные функции ==========
def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

def extract_features(text: str) -> dict:
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
    # эвристический fallback
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

# ========== Полная пост-обработка (v1.18) ==========
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

    if not ops:
        ops.append('synonyms')

    for op in ops:
        if op == 'synonyms':
            replacements = 0
            for pattern, syn_list in SYNONYMS:
                if random.random() < config.PROB_SYNONYMS:
                    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
                    if matches:
                        match = random.choice(matches)
                        word = match.group(0)
                        syn = random.choice(syn_list)
                        if word[0].isupper():
                            syn = syn.capitalize()
                        text = text[:match.start()] + syn + text[match.end():]
                        replacements += 1
            if replacements:
                logs.append(f"
