"""
Chapter Editor v4.8.0 — с автоматическим дообучением модели и сбором обратной связи
"""
import json
import os
import sys
import re
import logging
import random
import joblib
import subprocess
import csv
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

# ========== Пост-обработка (полная версия из v1.7) ==========
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
                if len(sent.split()) > 4 and random.random() < 0.25:
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
                if re.match(r'^[—"«]', sent) and random.random() < 0.2:
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

        # (отключённые операции не включены)

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
        feature_order = [
            'latin_ratio', 'marker_count', 'avg_word_len', 'max_repeat_ratio',
            'num_sentences', 'avg_sentence_len', 'dialog_ratio',
            'question_marks', 'exclamation_marks', 'lexical_diversity'
        ]

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
    return jsonify({"status": "ok", "message": "Feedback saved"})

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
        total = len(paragraphs)
        logger.info(f"Split into {total} paragraphs")

        def generate():
            try:
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем локальную обработку {total} абзацев (v4.8.0)..."})

                results = []
                for idx, para in enumerate(paragraphs):
                    yield _sse("paragraph_start", {
                        "index": idx,
                        "original": para,
                        "status": "processing"
                    })

                    try:
                        result = process_paragraph(para)
                    except Exception as e:
                        logger.error(f"Paragraph {idx} processing error: {e}")
                        result = {
                            "original": para,
                            "revised": para,
                            "status": "error",
                            "chain": "LOCAL",
                            "human_score": 0,
                            "logs": [f"Ошибка: {str(e)}"]
                        }
                    results.append(result)

                    yield _sse("paragraph_status", {
                        "index": idx,
                        "original": result["original"],
                        "revised": result["revised"],
                        "status": result["status"],
                        "chain": result["chain"],
                        "human_score": result.get("human_score", 0),
                        "logs": result.get("logs", [])
                    })

                    yield _sse("progress", {
                        "chars": idx + 1,
                        "estimated_total": total,
                        "percent": (idx + 1) / total * 100,
                        "log": f"Обработано {idx+1}/{total} абзацев"
                    })

                    yield _sse("paragraph_progress", {
                        "current": idx + 1,
                        "total": total,
                        "percent": (idx + 1) / total * 100
                    })

                final_text = "\n\n".join(r["revised"] for r in results)

                scores = [r.get("human_score", 0) for r in results if r.get("human_score", 0) > 0]
                avg_score = sum(scores) // len(scores) if scores else 0

                overall = analyze_overall(final_text)
                logger.info(f"Overall analysis: {overall}")

                status_counts = {"done": 0, "partial": 0, "error": 0}
                for r in results:
                    status_counts[r.get("status", "error")] += 1

                logger.info(f"Статусы абзацев: {status_counts}")

                yield _sse("done", {
                    "revised_text": final_text,
                    "original_text": chapter_text,
                    "summary": f"Обработано {total} абзацев (v4.8.0). Успешно: {status_counts['done']}, частично: {status_counts['partial']}, ошибок: {status_counts['error']}. Средний HUMAN: {avg_score}%",
                    "paragraphs": results,
                    "average_human_score": avg_score,
                    "overall_analysis": overall,
                    "status_counts": status_counts,
                    "checklist": []
                })
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
    return jsonify(detail=f"Server error: {str(e)}"), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
