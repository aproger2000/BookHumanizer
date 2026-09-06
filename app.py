"""
Chapter Editor v5.1.0 — расширенный поиск, сохранение лучшего текста
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
import requests
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

import config
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from db import init_db, get_all_experiments, save_experiment, set_state, get_state, get_best_experiment as db_get_best_experiment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "5.1.0"
MAX_CHARS = 30_000

PORT = os.environ.get('PORT', '8000')
BASE_URL = f"http://127.0.0.1:{PORT}"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

random.seed(config.RANDOM_SEED)

init_db()
from db import seed_experiments
seed_experiments()

logger.info(f"=== Chapter Editor v{APP_VERSION} ===")
logger.info(f"Config version: {config.CONFIG_VERSION}")
logger.info(f"Hypothesis: {config.HYPOTHESIS}")
logger.info(f"PORT: {PORT}")
logger.info(f"BASE_URL: {BASE_URL}")

# ========== Загрузка модели ==========
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

# ========== Словари ==========
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

_retrain_lock = threading.Lock()
auto_experiment_running = False
auto_experiment_lock = threading.Lock()

current_experiment_info = {
    'param_name': '',
    'param_value': 0.0,
    'last_score': 0,
    'best_score': 0,
    'total_done': 0,
    'total_planned': 0,
    'last_log': '',
    'best_text': ''   # текст лучшего эксперимента
}
status_lock = threading.Lock()

# ========== Функция переобучения ==========
def retrain_model_sync():
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
        joblib.dump(model, 'human_model.pkl')
        with open('feature_cols.txt', 'w') as f:
            f.write(','.join(feature_names))
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
            result = max(0, min(100, int(round(pred))))
            logger.debug(f"Model prediction: {result}%")
            return result
        except Exception as e:
            logger.warning(f"Ошибка предсказания: {e}. Использую эвристику.")
    else:
        logger.warning("Модель не загружена, используется эвристика.")
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

# ========== Пост-обработка ==========
def post_process(text: str, logs: list = None, params: dict = None) -> str:
    if not text or len(text) < 20:
        return text
    if logs is None:
        logs = []
    if params is None:
        params = {
            'PROB_SYNONYMS': config.PROB_SYNONYMS,
            'PROB_INSERTIONS': config.PROB_INSERTIONS,
            'PROB_SWAP_FIRST_WORDS': config.PROB_SWAP_FIRST_WORDS,
            'PROB_INTERJECTIONS': config.PROB_INTERJECTIONS,
            'PROB_PARTICLES': config.PROB_PARTICLES,
            'PROB_CANCEL_CANCEL': config.PROB_CANCEL_CANCEL,
            'PROB_REMOVE_AI_MARKERS': config.PROB_REMOVE_AI_MARKERS,
            'PROB_SPLIT_LONG_SENTENCES': config.PROB_SPLIT_LONG_SENTENCES,
            'PROB_ADD_COLLOQUIAL': config.PROB_ADD_COLLOQUIAL,
            'PROB_TYPOS': config.PROB_TYPOS,
            'PROB_SWAP_CLAUSES': config.PROB_SWAP_CLAUSES,
            'PROB_DIRECT_INDIRECT': config.PROB_DIRECT_INDIRECT,
        }
    ops = []
    if random.random() < params['PROB_SYNONYMS']:
        ops.append('synonyms')
    if random.random() < params['PROB_INSERTIONS']:
        ops.append('insertions')
    if random.random() < params['PROB_SWAP_FIRST_WORDS']:
        ops.append('swap_first_words')
    if random.random() < params['PROB_INTERJECTIONS']:
        ops.append('interjections')
    if random.random() < params['PROB_PARTICLES']:
        ops.append('insert_particles')
    if random.random() < params['PROB_CANCEL_CANCEL']:
        ops.append('cancel_cancel')
    if random.random() < params['PROB_REMOVE_AI_MARKERS']:
        ops.append('remove_ai_markers')
    if random.random() < params['PROB_SPLIT_LONG_SENTENCES']:
        ops.append('split_long_sentences')
    if random.random() < params['PROB_ADD_COLLOQUIAL']:
        ops.append('add_colloquial')
    if random.random() < params['PROB_TYPOS']:
        ops.append('add_typos')
    if random.random() < params['PROB_SWAP_CLAUSES']:
        ops.append('swap_clauses')
    if random.random() < params['PROB_DIRECT_INDIRECT']:
        ops.append('direct_indirect')
    if not ops:
        ops.append('synonyms')
    for op in ops:
        if op == 'synonyms':
            replacements = 0
            for pattern, syn_list in SYNONYMS:
                if random.random() < params['PROB_SYNONYMS']:
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
                logs.append(f"  - заменено синонимов: {replacements}")
        elif op == 'insertions':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            inserted = 0
            for sent in sentences:
                if len(sent.split()) > 5 and random.random() < 0.3:
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
        elif op == 'swap_first_words':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            swapped = 0
            for sent in sentences:
                if len(sent.split()) > 4 and random.random() < 0.3:
                    words = sent.split()
                    if len(words) >= 3 and not words[0].startswith(('—', '"', '«')):
                        words[0], words[1] = words[1], words[0]
                        sent = ' '.join(words)
                        swapped += 1
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if swapped:
                logs.append(f"  - перестановок первых слов: {swapped}")
        elif op == 'interjections':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            inserted_interj = 0
            for sent in sentences:
                if re.match(r'^[—"«]', sent) and random.random() < 0.25:
                    ins = random.choice(INTERJECTIONS)
                    match = re.search(r'^([—"«])\s*', sent)
                    if match:
                        prefix = match.group(0)
                        rest = sent[len(prefix):]
                        sent = prefix + ins + ', ' + rest[0].lower() + rest[1:] if rest else prefix + ins
                        inserted_interj += 1
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if inserted_interj:
                logs.append(f"  - вставлено междометий: {inserted_interj}")
        elif op == 'insert_particles':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            inserted_particles = 0
            for sent in sentences:
                if len(sent.split()) > 3 and random.random() < 0.3:
                    words = sent.split()
                    pos = random.randint(0, min(2, len(words)-1))
                    part = random.choice(PARTICLES)
                    words.insert(pos, part)
                    sent = ' '.join(words)
                    inserted_particles += 1
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if inserted_particles:
                logs.append(f"  - вставлено частиц: {inserted_particles}")
        elif op == 'cancel_cancel':
            replacements = 0
            for pattern, replacement in CANCEL_CANCEL_DICT:
                if re.search(pattern, text, flags=re.IGNORECASE):
                    text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
                    replacements += 1
            if replacements:
                logs.append(f"  - заменено канцеляризмов: {replacements}")
        elif op == 'remove_ai_markers':
            removed = 0
            for marker in AI_MARKERS:
                pattern = r'\s*' + re.escape(marker) + r'\s*,?\s*'
                if re.search(pattern, text, flags=re.IGNORECASE):
                    text = re.sub(pattern, '', text, flags=re.IGNORECASE)
                    removed += 1
            if removed:
                logs.append(f"  - удалено AI-маркеров: {removed}")
        elif op == 'split_long_sentences':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            split_count = 0
            for sent in sentences:
                words = sent.split()
                if len(words) > 25:
                    conjunctions = [' и ', ' а ', ' но ', ' что ', ' чтобы ', ' когда ', ' если ', ' потому что ']
                    best_pos = -1
                    for conj in conjunctions:
                        pos = sent.find(conj)
                        if pos != -1:
                            best_pos = pos
                            break
                    if best_pos != -1:
                        part1 = sent[:best_pos].strip()
                        part2 = sent[best_pos + len(conj):].strip()
                        if len(part1.split()) > 5 and len(part2.split()) > 5:
                            new_sentences.append(part1 + '.')
                            new_sentences.append(part2 + '.')
                            split_count += 1
                            continue
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if split_count:
                logs.append(f"  - разбито длинных предложений: {split_count}")
        elif op == 'add_colloquial':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            added = 0
            for sent in sentences:
                if len(sent.split()) > 3 and random.random() < 0.3:
                    words = sent.split()
                    pos = random.randint(0, min(2, len(words)-1))
                    particle = random.choice(COLLOQUIAL_PARTICLES)
                    words.insert(pos, particle)
                    sent = ' '.join(words)
                    added += 1
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if added:
                logs.append(f"  - добавлено разговорных частиц: {added}")
        elif op == 'add_typos':
            chars = list(text)
            typo_count = 0
            replacements = {
                'а': 'о', 'о': 'а', 'е': 'и', 'и': 'е',
                'н': 'т', 'т': 'н', 'с': 'з', 'з': 'с',
                'р': 'п', 'п': 'р', 'л': 'м', 'м': 'л',
                'в': 'б', 'б': 'в', 'к': 'н', 'н': 'к',
            }
            for i in range(len(chars)):
                if chars[i].isalpha() and random.random() < 0.02:
                    lower_char = chars[i].lower()
                    if lower_char in replacements:
                        replacement = replacements[lower_char]
                        if chars[i].isupper():
                            replacement = replacement.upper()
                        chars[i] = replacement
                        typo_count += 1
            text = ''.join(chars)
            if typo_count:
                logs.append(f"  - добавлено опечаток: {typo_count}")
        elif op == 'swap_clauses':
            def swap_clauses(text):
                patterns = [
                    (r'(.+?)\s+когда\s+(.+?)([.!?])', r'Когда \2, \1\3'),
                    (r'(.+?)\s+если\s+(.+?)([.!?])', r'Если \2, \1\3'),
                    (r'(.+?)\s+потому что\s+(.+?)([.!?])', r'Потому что \2, \1\3'),
                    (r'(.+?)\s+хотя\s+(.+?)([.!?])', r'Хотя \2, \1\3'),
                    (r'(.+?)\s+чтобы\s+(.+?)([.!?])', r'Чтобы \2, \1\3'),
                ]
                for pattern, repl in patterns:
                    text = re.sub(pattern, repl, text, flags=re.DOTALL)
                return text
            new_text = swap_clauses(text)
            if new_text != text:
                logs.append("  - перестановка частей (когда/если/потому что/хотя/чтобы)")
                text = new_text
        elif op == 'direct_indirect':
            def replace_direct_indirect(text):
                pattern = re.compile(r'—\s*(.+?)\s*,\s*—\s*(' + '|'.join(REPORTING_VERBS) + r')\s+([а-яА-ЯёЁ]+)\.?')
                def repl(m):
                    text_part = m.group(1).strip()
                    verb = m.group(2)
                    who = m.group(3)
                    if verb.endswith('а'):
                        who_form = who
                        if who in ('он', 'Алексей', 'Масарик', 'Кросс'):
                            who_form = 'она'
                    else:
                        who_form = who
                    return f"{who_form} {verb}, что {text_part.lower()}."
                return pattern.sub(repl, text)
            new_text = replace_direct_indirect(text)
            if new_text != text:
                logs.append("  - замена прямой речи на косвенную")
                text = new_text
    return text

# ========== Обработка абзаца ==========
def process_paragraph(paragraph: str, params: dict = None) -> dict:
    if not paragraph:
        return {"original": paragraph, "revised": paragraph, "status": "error", "chain": "LOCAL", "human_score": 0, "logs": ["Пустой абзац"]}
    logs = []
    original_score = get_human_score(paragraph)
    logs.append(f"Оригинальный HUMAN: {original_score}%")
    if original_score >= 50:
        return {"original": paragraph, "revised": paragraph, "status": "done", "chain": "LOCAL (skipped)", "human_score": original_score, "logs": logs + ["Абзац уже имеет HUMAN >= 50, пропущен"]}
    post_logs = []
    revised = post_process(paragraph, logs=post_logs, params=params)
    score = get_human_score(revised)
    logs.extend(post_logs)
    logs.append(f"Итоговый HUMAN: {score}%")
    return {"original": paragraph, "revised": revised, "status": "done" if score > 50 else "partial", "chain": "LOCAL (post only)", "human_score": score, "logs": logs}

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

def split_paragraphs(text: str) -> list:
    if not text:
        return []
    text = text.replace('\r\n', '\n')
    paragraphs = text.split('\n\n')
    return [p.strip() for p in paragraphs if p.strip()]

# ========== Flask endpoints ==========
@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION, config_version=config.CONFIG_VERSION, hypothesis=config.HYPOTHESIS)

RETRAIN_TOKEN = os.environ.get("RETRAIN_TOKEN", "your-secret-token")

@app.post("/api/retrain")
def api_retrain():
    token = request.headers.get("X-Retrain-Token")
    if token != RETRAIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        retrain_model_sync()
        return jsonify({"status": "ok", "message": "Retrained successfully"})
    except Exception as e:
        logger.error(f"Retrain error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/reload_model")
def reload_model():
    try:
        load_model()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/feedback")
def feedback():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON data"}), 400
    revised_text = data.get('revised_text', '').strip()
    yandex_score = data.get('yandex_score')
    if not revised_text:
        return jsonify({"error": "Missing revised_text"}), 400
    if yandex_score is None:
        return jsonify({"error": "Missing yandex_score"}), 400
    try:
        yandex_score = int(yandex_score)
        if not (0 <= yandex_score <= 100):
            raise ValueError
    except:
        return jsonify({"error": "yandex_score must be an integer 0-100"}), 400
    csv_path = Path('training_data.csv')
    if csv_path.exists():
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 12 and row[10] == revised_text:
                    return jsonify({"warning": "This text already exists in training data", "status": "skipped"}), 200
    try:
        features = extract_features(revised_text)
    except Exception as e:
        logger.error(f"Feature extraction failed: {e}")
        return jsonify({"error": f"Feature extraction failed: {str(e)}"}), 500
    if MODEL_LOADED and feature_cols:
        feature_order = feature_cols
    else:
        feature_order = ['latin_ratio', 'marker_count', 'avg_word_len', 'max_repeat_ratio', 'num_sentences', 'avg_sentence_len', 'dialog_ratio', 'question_marks', 'exclamation_marks', 'lexical_diversity']
    row = [features.get(col, 0) for col in feature_order] + [revised_text, yandex_score]
    if not csv_path.exists():
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            header = feature_order + ['processed_text', 'HUMAN_yandex']
            writer.writerow(header)
            writer.writerow(row)
    else:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)
    logger.info(f"Added feedback: score={yandex_score}, text length={len(revised_text)}")
    thread = threading.Thread(target=retrain_model_sync, daemon=True)
    thread.start()
    logger.info("Запущено фоновое переобучение после добавления оценки")
    return jsonify({"status": "ok", "message": "Feedback saved, retraining started"})

@app.post("/api/revise")
def api_revise():
    logger.info(f"=== api_revise: START (v{APP_VERSION}) ===")
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
        if not paragraphs:
            paragraphs = [chapter_text]
        total = len(paragraphs)
        logger.info(f"Split into {total} paragraphs")
        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем локальную обработку {total} абзацев (v{APP_VERSION})..."})
                results = []
                for idx, para in enumerate(paragraphs):
                    yield _sse("paragraph_start", {"index": idx, "original": para, "status": "processing"})
                    try:
                        result = process_paragraph(para)
                    except Exception as e:
                        logger.error(f"Paragraph {idx} processing error: {e}")
                        result = {"original": para, "revised": para, "status": "error", "chain": "LOCAL", "human_score": 0, "logs": [f"Ошибка: {str(e)}"]}
                    results.append(result)
                    yield _sse("paragraph_status", {"index": idx, "original": result["original"], "revised": result["revised"], "status": result["status"], "chain": result["chain"], "human_score": result.get("human_score", 0), "logs": result.get("logs", [])})
                    yield _sse("progress", {"chars": idx + 1, "estimated_total": total, "percent": (idx + 1) / total * 100, "log": f"Обработано {idx+1}/{total} абзацев"})
                    yield _sse("paragraph_progress", {"current": idx + 1, "total": total, "percent": (idx + 1) / total * 100})
                final_text = "\n\n".join(r["revised"] for r in results)
                scores = [r.get("human_score", 0) for r in results if r.get("human_score", 0) > 0]
                avg_score = sum(scores) // len(scores) if scores else 0
                overall = analyze_overall(final_text)
                logger.info(f"Overall analysis: {overall}")
                status_counts = {"done": 0, "partial": 0, "error": 0}
                for r in results:
                    status_counts[r.get("status", "error")] += 1
                logger.info(f"Статусы абзацев: {status_counts}")
                yield _sse("done", {"revised_text": final_text, "original_text": chapter_text, "summary": f"Обработано {total} абзацев (v{APP_VERSION}). Успешно: {status_counts['done']}, частично: {status_counts['partial']}, ошибок: {status_counts['error']}. Средний HUMAN: {avg_score}%", "paragraphs": results, "average_human_score": avg_score, "overall_analysis": overall, "status_counts": status_counts, "checklist": []})
            except Exception as e:
                logger.exception("Unexpected error in generate")
                yield _sse("error", {"detail": f"Unexpected error: {str(e)}"})
        return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as e:
        logger.exception("api_revise: Unexpected error")
        return jsonify(detail=f"Server error: {str(e)}"), 500

# ========== Эндпоинты для экспериментов ==========
@app.post("/api/revise_internal")
def revise_internal():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON"}), 400
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"error": "No text"}), 400
    params = data.get('params', {})
    logger.info(f"revise_internal: входной текст длиной {len(text)} символов")
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        logger.info("split_paragraphs вернул пустой список, обрабатываем весь текст как один абзац")
        paragraphs = [text]
    logger.info(f"revise_internal: найдено {len(paragraphs)} абзацев")
    results = []
    for idx, para in enumerate(paragraphs):
        if not para:
            continue
        logger.info(f"revise_internal: обрабатываем абзац {idx+1} длиной {len(para)} символов")
        result = process_paragraph(para, params=params)
        results.append(result)
        logger.info(f"revise_internal: абзац {idx+1} обработан, длина результата {len(result['revised'])}")
    final_text = "\n\n".join(r["revised"] for r in results)
    logger.info(f"revise_internal: итоговый текст длиной {len(final_text)} символов")
    return jsonify({"revised_text": final_text})

@app.get("/api/experiments")
def get_experiments():
    rows = get_all_experiments()
    experiments = []
    for row in rows:
        experiments.append({
            'id': row[0],
            'config_name': row[1],
            'params': json.loads(row[2]) if row[2] else {},
            'human': row[3] or 0,
            'likely_human': row[4] or 0,
            'likely_ai': row[5] or 0,
            'ai': row[6] or 0,
            'timestamp': row[7],
            'status': row[8]
        })
    return jsonify(experiments)

@app.get("/api/experiments/best")
def api_best_experiment():
    # Импортируем функцию из db.py с другим именем
    from db import get_best_experiment as db_get_best
    best = db_get_best()
    if best:
        params = {}
        if best[2]:
            try:
                params = json.loads(best[2])
            except:
                params = {}
        return jsonify({
            'id': best[0],
            'config_name': best[1],
            'params': params,
            'human': best[3] or 0,
            'likely_human': best[4] or 0,
            'likely_ai': best[5] or 0,
            'ai': best[6] or 0,
            'timestamp': best[7],
            'status': best[8],
            'revised_text': best[9] if len(best) > 9 else ''
        })
    return jsonify(None)

@app.post("/api/experiments/start")
def start_auto():
    global auto_experiment_running
    with auto_experiment_lock:
        if auto_experiment_running:
            return jsonify({"status": "already running"})
        auto_experiment_running = True
        thread = threading.Thread(target=run_auto_loop, daemon=True)
        thread.start()
        logger.info("Автоматический цикл экспериментов запущен")
        return jsonify({"status": "started"})

@app.post("/api/experiments/stop")
def stop_auto():
    global auto_experiment_running
    with auto_experiment_lock:
        auto_experiment_running = False
        logger.info("Автоматический цикл экспериментов остановлен")
        return jsonify({"status": "stopped"})

@app.get("/api/experiments/status")
def status_auto():
    with status_lock:
        info = {
            "running": auto_experiment_running,
            "param_name": current_experiment_info.get('param_name', ''),
            "param_value": current_experiment_info.get('param_value', 0.0),
            "last_score": current_experiment_info.get('last_score', 0),
            "best_score": current_experiment_info.get('best_score', 0),
            "total_done": current_experiment_info.get('total_done', 0),
            "total_planned": current_experiment_info.get('total_planned', 0),
            "last_log": current_experiment_info.get('last_log', ''),
            "best_text": current_experiment_info.get('best_text', '')  # добавили
        }
    return jsonify(info)

# ========== Параметры оптимизации ==========
PARAMS_TO_OPTIMIZE = [
    ('PROB_SYNONYMS', 0.3, 0.3, 0.7, 0.05),
    ('PROB_TYPOS', 0.3, 0.2, 0.7, 0.05),
    ('PROB_PARTICLES', 0.25, 0.15, 0.5, 0.05),
    ('PROB_INTERJECTIONS', 0.25, 0.15, 0.6, 0.05),   # теперь до 0.6
    ('PROB_SWAP_FIRST_WORDS', 0.3, 0.2, 0.6, 0.05),
    ('PROB_INSERTIONS', 0.3, 0.2, 0.6, 0.05),
    ('PROB_ADD_COLLOQUIAL', 0.3, 0.2, 0.5, 0.05),
]

TEST_TEXT = None

def load_test_text():
    """Всегда читает файл test_text.txt, не использует кэш."""
    text_file = Path('test_text.txt')
    logger.info(f"Пытаемся загрузить файл: {text_file.absolute()}")
    if text_file.exists():
        with open(text_file, 'r', encoding='utf-8') as f:
            content = f.read()
        logger.info(f"Загружен тестовый текст из файла, длина: {len(content)} символов")
        return content
    else:
        logger.warning("test_text.txt не найден, используется заглушка")
        return "За восемь лет до «Стеклянного Ливня» Храм Солнца встретил Алексея..."

def run_auto_loop():
    global auto_experiment_running, current_experiment_info
    logger.info("Авто-цикл начал работу (v5.1.0)")

    # Импортируем парсер Яндекс.Нейродетектора
    try:
        from yandex_parser import parse_yandex_neuro
        logger.info("Парсер Яндекс.Нейродетектора загружен")
    except ImportError as e:
        parse_yandex_neuro = None
        logger.warning(f"Не удалось загрузить yandex_parser: {e}")

    def get_local_score(text):
        return get_human_score(text)

    text = load_test_text()
    logger.info(f"Текст загружен, длина: {len(text)} символов, первые 100 символов: {repr(text[:100])}")
    if not text:
        logger.error("Не удалось загрузить тестовый текст")
        return

    total_planned = 0
    for _, _, min_val, max_val, step in PARAMS_TO_OPTIMIZE:
        total_planned += int((max_val - min_val) / step) + 1
    logger.info(f"Всего запланировано экспериментов: {total_planned}")

    with status_lock:
        current_experiment_info['total_planned'] = total_planned
        current_experiment_info['total_done'] = 0
        current_experiment_info['best_score'] = 0
        current_experiment_info['last_score'] = 0
        current_experiment_info['best_text'] = ''

    current_idx = int(get_state('current_idx') or 0)
    current_value = float(get_state('current_value') or PARAMS_TO_OPTIMIZE[current_idx][1])
    best_value = float(get_state('best_value') or current_value)
    best_score = float(get_state('best_score') or 0)

    while auto_experiment_running:
        if best_score >= 70:
            logger.info("Достигнута цель HUMAN+LIKELY_HUMAN >= 70%, цикл остановлен.")
            break

        param_name, base_val, min_val, max_val, step = PARAMS_TO_OPTIMIZE[current_idx]
        new_value = current_value + step
        if new_value > max_val:
            current_idx = (current_idx + 1) % len(PARAMS_TO_OPTIMIZE)
            new_value = PARAMS_TO_OPTIMIZE[current_idx][1]
            set_state('current_idx', str(current_idx))
            set_state('current_value', str(new_value))
            set_state('best_value', str(new_value))
            set_state('best_score', str(best_score))
            logger.info(f"Переход к параметру {PARAMS_TO_OPTIMIZE[current_idx][0]}")
            continue

        params = {
            'PROB_SYNONYMS': config.PROB_SYNONYMS,
            'PROB_INSERTIONS': config.PROB_INSERTIONS,
            'PROB_SWAP_FIRST_WORDS': config.PROB_SWAP_FIRST_WORDS,
            'PROB_INTERJECTIONS': config.PROB_INTERJECTIONS,
            'PROB_PARTICLES': config.PROB_PARTICLES,
            'PROB_CANCEL_CANCEL': config.PROB_CANCEL_CANCEL,
            'PROB_REMOVE_AI_MARKERS': config.PROB_REMOVE_AI_MARKERS,
            'PROB_SPLIT_LONG_SENTENCES': config.PROB_SPLIT_LONG_SENTENCES,
            'PROB_ADD_COLLOQUIAL': config.PROB_ADD_COLLOQUIAL,
            'PROB_TYPOS': config.PROB_TYPOS,
            'PROB_SWAP_CLAUSES': 0.0,
            'PROB_DIRECT_INDIRECT': 0.0,
        }
        params[param_name] = new_value

        logger.info(f"Запуск эксперимента: {param_name} = {new_value:.2f}")
        with status_lock:
            current_experiment_info['param_name'] = param_name
            current_experiment_info['param_value'] = new_value
            current_experiment_info['last_log'] = f"Запуск {param_name} = {new_value:.2f}"

        try:
            resp = requests.post(
                f"{BASE_URL}/api/revise_internal",
                json={'text': text, 'params': params, 'style': 'neutral'},
                timeout=60
            )
            if resp.status_code != 200:
                logger.error(f"Ошибка revise_internal: {resp.status_code}")
                human = get_local_score(text)
                likely_human = 0
                ai = 100 - human
                processed_text = text
            else:
                data = resp.json()
                processed_text = data.get('revised_text')
                logger.info(f"Длина обработанного текста: {len(processed_text) if processed_text else 0}")
                if not processed_text:
                    human = get_local_score(text)
                    likely_human = 0
                    ai = 100 - human
                else:
                    # Пытаемся получить оценку через Яндекс.Нейродетектор
                    if parse_yandex_neuro:
                        try:
                            if len(processed_text) < 150:
                                logger.warning(f"Текст слишком короткий ({len(processed_text)} символов), используем локальный")
                                human = get_local_score(processed_text)
                                likely_human = 0
                                ai = 100 - human
                            else:
                                yandex_result = parse_yandex_neuro(processed_text)
                                human = yandex_result.get('human', 0)
                                likely_human = yandex_result.get('likely_human', 0)
                                likely_ai = yandex_result.get('likely_ai', 0)
                                ai = yandex_result.get('ai', 0)
                                logger.info(f"Оценка Яндекса: HUMAN={human}%, LIKELY_HUMAN={likely_human}%")
                        except Exception as e:
                            logger.warning(f"Ошибка парсинга Яндекса: {e}, используем локальный")
                            human = get_local_score(processed_text)
                            likely_human = 0
                            likely_ai = 0
                            ai = 100 - human
                    else:
                        human = get_local_score(processed_text)
                        likely_human = 0
                        likely_ai = 0
                        ai = 100 - human

            score = human + likely_human
            logger.info(f"Результат: HUMAN={human}%, LIKELY_HUMAN={likely_human}%, сумма={score}%")

            # Сохраняем в БД
            try:
                save_experiment(
                    config_name=f"auto_{param_name}_{new_value:.2f}",
                    params=params,
                    results={'human': human, 'likely_human': likely_human, 'likely_ai': likely_ai if 'likely_ai' in locals() else 0, 'ai': ai},
                    status='done',
                    revised_text=processed_text if processed_text else ''
                )
                logger.info(f"Эксперимент сохранён в БД: {param_name}={new_value:.2f}")
            except Exception as e:
                logger.error(f"Ошибка сохранения эксперимента: {e}")

            # Отправляем feedback для дообучения локального детектора
            if processed_text and processed_text != text:
                try:
                    requests.post(
                        f"{BASE_URL}/api/feedback",
                        json={'revised_text': processed_text, 'yandex_score': human},
                        timeout=30
                    )
                except Exception as e:
                    logger.warning(f"Feedback error: {e}")

            with status_lock:
                current_experiment_info['last_score'] = score
                if score > best_score:
                    current_experiment_info['best_score'] = score
                    current_experiment_info['best_text'] = processed_text if processed_text else ''
                current_experiment_info['total_done'] += 1

            # Обновляем лучший результат
            if score > best_score:
                best_score = score
                best_value = new_value
                set_state('best_value', str(best_value))
                set_state('best_score', str(best_score))
                set_state('current_value', str(new_value))
                logger.info(f"Улучшение! Новый лучший для {param_name}: {best_value} (score {best_score})")
            else:
                set_state('current_value', str(best_value))
                current_idx = (current_idx + 1) % len(PARAMS_TO_OPTIMIZE)
                set_state('current_idx', str(current_idx))
                new_val = PARAMS_TO_OPTIMIZE[current_idx][1]
                set_state('current_value', str(new_val))
                set_state('best_value', str(new_val))
                set_state('best_score', str(best_score))
                logger.info(f"Ухудшение, переходим к {PARAMS_TO_OPTIMIZE[current_idx][0]}")

        except Exception as e:
            logger.exception(f"Ошибка в цикле: {e}")
            set_state('current_value', str(best_value))
            current_idx = (current_idx + 1) % len(PARAMS_TO_OPTIMIZE)
            set_state('current_idx', str(current_idx))
            new_val = PARAMS_TO_OPTIMIZE[current_idx][1]
            set_state('current_value', str(new_val))
            set_state('best_value', str(new_val))
            set_state('best_score', str(best_score))

        time.sleep(5)

    logger.info("Авто-цикл завершён")

# ========== Статика ==========
@app.get("/")
def index():
    return app.send_static_file("index.html")

@app.errorhandler(Exception)
def handle_exception(exc):
    logger.exception("Unhandled exception")
    return jsonify(detail=f"Server error: {str(exc)}"), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
