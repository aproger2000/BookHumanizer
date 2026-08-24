"""
Chapter Editor v4.2.0 — улучшенная пост-обработка с вариативностью
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

APP_VERSION = "4.2.0"
MAX_CHARS = 30_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Расширенный словарь синонимов
SYNONYMS = {
    r'\bсказал\b': ['произнёс', 'бросил', 'выдохнул', 'усмехнулся', 'пробормотал', 'отозвался', 'вымолвил', 'проговорил', 'процедил', 'буркнул'],
    r'\bсказала\b': ['произнесла', 'бросила', 'выдохнула', 'усмехнулась', 'пробормотала', 'отозвалась', 'вымолвила', 'проговорила', 'процедила', 'буркнула'],
    r'\bспросил\b': ['поинтересовался', 'осведомился', 'полюбопытствовал', 'задал вопрос', 'переспросил', 'допытывался'],
    r'\bспросила\b': ['поинтересовалась', 'осведомилась', 'полюбопытствовала', 'задала вопрос', 'переспросила', 'допытывалась'],
    r'\bответил\b': ['откликнулся', 'парировал', 'возразил', 'подтвердил', 'отвечал', 'возразил'],
    r'\bответила\b': ['откликнулась', 'парировала', 'возразила', 'подтвердила', 'отвечала', 'возразила'],
    r'\bочень\b': ['весьма', 'крайне', 'чрезвычайно', 'невероятно', 'до крайности', 'безмерно'],
    r'\bхорошо\b': ['превосходно', 'отлично', 'замечательно', 'классно', 'великолепно', 'блестяще'],
    r'\bплохо\b': ['скверно', 'неважно', 'так себе', 'дурно', 'отвратительно'],
    r'\bбыстро\b': ['стремительно', 'мгновенно', 'рывком', 'проворно', 'бегом'],
    r'\bмедленно\b': ['неспешно', 'неторопливо', 'вяло', 'лениво', 'черепашьим шагом'],
    r'\bбольшой\b': ['огромный', 'громадный', 'колоссальный', 'грандиозный', 'необъятный', 'гигантский'],
    r'\bмаленький\b': ['крошечный', 'миниатюрный', 'небольшой', 'малюсенький', 'крохотный', 'микроскопический'],
    r'\bсмотреть\b': ['вглядываться', 'всматриваться', 'наблюдать', 'глазеть', 'рассматривать', 'созерцать'],
    r'\bувидел\b': ['заметил', 'приметил', 'углядел', 'узрел', 'увидал', 'разглядел'],
    r'\bпонял\b': ['осознал', 'сообразил', 'смекнул', 'догадался', 'уразумел', 'вник'],
    r'\bдумать\b': ['размышлять', 'соображать', 'прикидывать', 'считать', 'полагать', 'думать'],
    r'\bзнать\b': ['ведать', 'понимать', 'осознавать', 'догадываться', 'представлять', 'знать'],
    r'\bидти\b': ['шагать', 'двигаться', 'направляться', 'топать', 'брести', 'шествовать'],
    r'\bстоять\b': ['выситься', 'возвышаться', 'торчать', 'находиться', 'располагаться', 'стоять'],
    r'\bсидеть\b': ['восседать', 'расположиться', 'устроиться', 'плюхнуться', 'примоститься', 'сидеть'],
    r'\bлежать\b': ['покоиться', 'валяться', 'возлежать', 'растянуться', 'простираться', 'лежать'],
    r'\bснова\b': ['опять', 'вновь', 'заново', 'сызнова', 'повторно', 'опять-таки'],
    r'\bтолько\b': ['лишь', 'едва', 'всего лишь', 'только что', 'единственно', 'исключительно'],
    r'\bвдруг\b': ['неожиданно', 'внезапно', 'врасплох', 'как гром среди ясного неба', 'отчего-то', 'вдруг'],
    r'\bконечно\b': ['разумеется', 'естественно', 'безусловно', 'ясное дело', 'само собой', 'без сомнения'],
    r'\bвозможно\b': ['вероятно', 'похоже', 'должно быть', 'наверное', 'пожалуй', 'может быть'],
    r'\bпоэтому\b': ['потому', 'оттого', 'следовательно', 'стало быть', 'значит', 'следовательно'],
    r'\bпросто\b': ['всего-навсего', 'элементарно', 'банально', 'обыкновенно', 'попросту'],
    r'\bсовсем\b': ['вовсе', 'абсолютно', 'совершенно', 'полностью', 'целиком'],
    r'\bпочти\b': ['едва ли не', 'практически', 'без малого', 'чуть ли не', 'почти что'],
}

INSERTIONS = ['впрочем', 'кстати', 'разумеется', 'пожалуй', 'кажется', 'несомненно', 'в общем', 'между прочим', 'надо сказать', 'честно говоря', 'к слову']
INTERJECTIONS = ['ах', 'ой', 'ну', 'вот', 'эй', 'увы', 'о', 'ага', 'ух']

def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

def get_human_score(text: str) -> int:
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
    Улучшенная пост-обработка с вариативностью:
    - синонимы: 50% слов
    - вводные: 25% предложений
    - перестановки: 30% предложений
    - междометия: 15% диалогов
    - перестановка частей: 20% предложений с 'когда'
    - замена прямой/косвенной речи: 20% диалогов (экспериментально)
    Для каждого абзаца выбирается случайный набор операций.
    """
    if not text or len(text) < 20:
        return text

    if logs is None:
        logs = []

    ops = []
    if random.random() < 0.9:
        ops.append('synonyms')
    if random.random() < 0.5:
        ops.append('insertions')
    if random.random() < 0.6:
        ops.append('swap_first_words')
    if random.random() < 0.3:
        ops.append('interjections')
    if random.random() < 0.4:
        ops.append('swap_clauses')
    if random.random() < 0.3:
        ops.append('direct_indirect')

    if not ops:
        ops.append('synonyms')

    for op in ops:
        if op == 'synonyms':
            words = text.split(' ')
            new_words = []
            replacements = 0
            for word in words:
                clean = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
                if clean.lower() in SYNONYMS and random.random() < 0.5:
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
                if len(sent.split()) > 5 and random.random() < 0.25:
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
                if re.match(r'^[—"«]', sent) and random.random() < 0.15:
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
            # Перестановка частей с 'когда'
            def swap_clauses(text):
                pattern = re.compile(r'(.+?)\s+когда\s+(.+?)([.!?])', re.DOTALL)
                def repl(m):
                    first = m.group(1).strip()
                    second = m.group(2).strip()
                    punct = m.group(3)
                    if first.startswith(('—', '"', '«')):
                        return m.group(0)
                    return f"Когда {second}, {first}{punct}"
                return pattern.sub(repl, text)
            new_text = swap_clauses(text)
            if new_text != text:
                logs.append("  - перестановка частей (когда)")
                text = new_text

        elif op == 'direct_indirect':
            # Замена прямой речи на косвенную (упрощённо)
            def replace_direct_indirect(text):
                pattern = re.compile(r'—\s*(.+?)\s*,\s*—\s*(сказал|сказала|произнёс|произнесла|ответил|ответила|спросил|спросила)\s+([а-яА-ЯёЁ]+)\.?')
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
        "chain": "LOCAL (v4.2.0)",
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
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем обработку {total} абзацев (v4.2.0)..."})

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

                status_counts = {"done": 0, "partial": 0, "error": 0}
                for r in results:
                    status_counts[r.get("status", "error")] += 1

                logger.info(f"Статусы абзацев: {status_counts}")

                yield _sse("done", {
                    "revised_text": final_text,
                    "original_text": chapter_text,
                    "summary": f"Обработано {total} абзацев (v4.2.0). Успешно: {status_counts['done']}, частично: {status_counts['partial']}, ошибок: {status_counts['error']}. Средний HUMAN: {avg_score}%",
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
    return jsonify(detail=f"Server error: {str(exc)}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
