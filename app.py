"""
Chapter Editor v4.4.0 — с вынесенными параметрами в config.py
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

APP_VERSION = "4.4.0"
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

# ========== Используем словари из config ==========
SYNONYMS = config.SYNONYMS_DICT
INSERTIONS = config.INSERTIONS_LIST
INTERJECTIONS = config.INTERJECTIONS_LIST
PARTICLES = config.PARTICLES_LIST
ADVERBS = config.ADVERBS_LIST
REPORTING_VERBS = config.REPORTING_VERBS
CLAUSE_CONJUNCTIONS = config.CLAUSE_CONJUNCTIONS

# ========== Вспомогательные функции ==========
def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def extract_features(text: str) -> dict:
    # (без изменений, та же, что была)
    ...


def get_human_score(text: str) -> int:
    # (без изменений)
    ...


def post_process(text: str, logs: list = None) -> str:
    """
    Пост-обработка с параметрами из config.py
    """
    if not text or len(text) < 20:
        return text
    if logs is None:
        logs = []

    # Собираем список операций на основе вероятностей из config
    ops = []
    if random.random() < config.PROB_SYNONYMS:           # 0.5
        ops.append('synonyms')
    if random.random() < config.PROB_INSERTIONS:         # 0.3
        ops.append('insertions')
    if random.random() < config.PROB_SWAP_FIRST_WORDS:   # 0.25
        ops.append('swap_first_words')
    if random.random() < config.PROB_INTERJECTIONS:      # 0.2
        ops.append('interjections')
    if random.random() < config.PROB_SWAP_CLAUSES:       # 0.6
        ops.append('swap_clauses')
    if random.random() < config.PROB_DIRECT_INDIRECT:    # 0.45
        ops.append('direct_indirect')
    if random.random() < config.PROB_INVERSION:          # 0.25
        ops.append('inversion')
    if random.random() < config.PROB_SWAP_SUBJECT_PREDICATE:  # 0.15
        ops.append('swap_subject_predicate')
    if random.random() < config.PROB_PARTICLES:          # 0.15
        ops.append('insert_particles')

    if not ops:
        ops.append('synonyms')  # гарантия хоть какой-то обработки

    # Применяем операции
    for op in ops:
        if op == 'synonyms':
            words = text.split(' ')
            new_words = []
            replacements = 0
            for word in words:
                clean = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
                if clean.lower() in SYNONYMS and random.random() < 0.5:  # оставляем 0.5
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

        elif op == 'inversion':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            inverted = 0
            for sent in sentences:
                words = sent.split()
                if len(words) >= 4:
                    for i in range(len(words)-1, max(0, len(words)-3), -1):
                        if words[i].lower().rstrip('.,!?') in ADVERBS:
                            adv = words.pop(i)
                            adv_clean = adv.rstrip('.,!?')
                            words.insert(0, adv_clean + ',')
                            sent = ' '.join(words)
                            inverted += 1
                            break
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if inverted:
                logs.append(f"  - инверсий (вынос обстоятельства): {inverted}")

        elif op == 'swap_subject_predicate':
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_sentences = []
            swapped_sp = 0
            for sent in sentences:
                if len(sent.split()) >= 4:
                    words = sent.split()
                    if not words[0].startswith(('—', '"', '«')):
                        for i in range(0, min(3, len(words)-2)):
                            if words[i].lower() in ['он', 'она', 'оно', 'они', 'мы', 'вы', 'ты', 'я', 'алексей', 'анна', 'рио', 'лео', 'кросс', 'масарик', 'надира']:
                                for j in range(i+1, min(i+4, len(words))):
                                    if words[j].endswith(('ить', 'ать', 'ять', 'еть', 'уть', 'чь')):
                                        words[i], words[j] = words[j], words[i]
                                        sent = ' '.join(words)
                                        swapped_sp += 1
                                        break
                                break
                new_sentences.append(sent)
            text = '. '.join(new_sentences)
            if swapped_sp:
                logs.append(f"  - перестановок подлежащего/сказуемого: {swapped_sp}")

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

    return text


# ========== Остальные функции (без изменений) ==========
def split_paragraphs(text: str) -> list:
    ...

def process_paragraph(paragraph: str) -> dict:
    ...

def analyze_overall(text: str) -> dict:
    ...

@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)

@app.post("/api/revise")
def api_revise():
    # (полная версия, как в предыдущем ответе)
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
