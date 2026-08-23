"""
Chapter Editor v3.10.1 — улучшенный локальный синонимайзер + LibreTranslate fallback
"""
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

APP_VERSION = "3.10.1"
MAX_CHARS = 30_000
CHUNK_SIZE = 3000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# === УЛУЧШЕННЫЙ ЛОКАЛЬНЫЙ СИНОНИМАЙЗЕР ===
def apply_local_synonyms(text: str) -> str:
    """Значительно изменяет текст, заменяя слова, фразы и добавляя вводные конструкции."""
    if not text:
        return text

    # Замены отдельных слов
    word_synonyms = {
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
    }

    # Замены целых фраз
    phrase_synonyms = {
        r'в конце концов': ['в итоге', 'в конечном счёте', 'в результате'],
        r'с самого начала': ['изначально', 'сразу же', 'с первых шагов'],
        r'в одно мгновение': ['мгновенно', 'вмиг', 'в один миг'],
        r'время от времени': ['иногда', 'изредка', 'временами'],
        r'так или иначе': ['в любом случае', 'как бы там ни было'],
        r'как правило': ['обычно', 'чаще всего'],
        r'в целом': ['в общем', 'в основном'],
        r'на самом деле': ['фактически', 'по сути', 'в действительности'],
        r'всё равно': ['тем не менее', 'однако', 'всё же'],
        r'к тому же': ['кроме того', 'более того', 'вдобавок'],
    }

    # 1. Замена фраз (с вероятностью 50%)
    for pattern, variants in phrase_synonyms.items():
        if random.random() < 0.5:
            text = re.sub(pattern, random.choice(variants), text, flags=re.IGNORECASE)

    # 2. Замена слов (с вероятностью 40%)
    words = text.split(' ')
    new_words = []
    for word in words:
        clean_word = re.sub(r'[^a-zA-Zа-яА-Я]', '', word)
        if clean_word.lower() in word_synonyms and random.random() < 0.4:
            syn = random.choice(word_synonyms[clean_word.lower()])
            if clean_word[0].isupper():
                syn = syn.capitalize()
            suffix = word[len(clean_word):]
            new_words.append(syn + suffix)
        else:
            new_words.append(word)
    text = ' '.join(new_words)

    # 3. Добавление вводных слов (в 30% предложений)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    insertions = ['впрочем', 'кстати', 'разумеется', 'пожалуй', 'кажется', 'несомненно']
    new_sentences = []
    for sent in sentences:
        if len(sent.split()) > 5 and random.random() < 0.3:
            words = sent.split()
            pos = random.randint(1, min(3, len(words)-1))
            ins = random.choice(insertions)
            words.insert(pos, ins + ',')
            sent = ' '.join(words)
        new_sentences.append(sent)
    text = '. '.join(new_sentences)

    return text


# === ПЕРЕВОДЧИК С FALLBACK НА LIBRETRANSLATE ===
def translate_with_fallback(text: str, target_lang: str = "en", max_retries: int = 3) -> str:
    if not text or len(text.strip()) < 2:
        return text

    # 1. Google Translate
    url_google = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_lang,
        "dt": "t",
        "q": text
    }

    for attempt in range(max_retries):
        try:
            resp = requests.get(url_google, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                translated = "".join(item[0] for item in data[0] if item[0])
                if translated:
                    return translated
            elif resp.status_code == 429:
                wait = (2 ** attempt) + random.random() * 2
                logger.warning(f"Google 429 (attempt {attempt+1}), waiting {wait:.1f}s")
                time.sleep(wait)
                continue
            else:
                logger.warning(f"Google attempt {attempt+1} failed: {resp.status_code}")
                time.sleep(1 + random.random())
        except Exception as e:
            logger.warning(f"Google exception: {e}")
            time.sleep(1 + random.random())

    # 2. MyMemory (POST)
    try:
        url_mymemory = "https://api.mymemory.translated.net/get"
        payload = {
            "q": text,
            "langpair": f"auto|{target_lang}",
            "de": "user@example.com"
        }
        resp = requests.post(url_mymemory, data=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("responseStatus") == 200:
                translated = data.get("responseData", {}).get("translatedText")
                if translated:
                    return translated
        logger.warning(f"MyMemory failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"MyMemory exception: {e}")

    # 3. LibreTranslate (публичный экземпляр)
    try:
        url_lt = "https://libretranslate.com/translate"
        payload = {
            "q": text,
            "source": "auto",
            "target": target_lang,
            "format": "text"
        }
        resp = requests.post(url_lt, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            translated = data.get("translatedText")
            if translated:
                return translated
        logger.warning(f"LibreTranslate failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"LibreTranslate exception: {e}")

    logger.warning(f"All translation attempts failed for {target_lang}. Returning original.")
    return text


def translate_chunk(text: str, chain: list) -> str:
    if not text or len(text.strip()) < 2:
        return text
    try:
        current = text
        for lang in chain:
            current = translate_with_fallback(current, target_lang=lang)
        ru = translate_with_fallback(current, target_lang="ru")
        return ru
    except Exception as e:
        logger.error(f"Chain {chain} error: {e}")
        return text


def split_paragraphs(text: str) -> list:
    if not text:
        return []
    text = text.replace('\r\n', '\n')
    paragraphs = text.split('\n\n')
    return [p.strip() for p in paragraphs if p.strip()]


# === ВСТРОЕННЫЙ ДЕТЕКТОР HUMAN SCORE ===
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


def process_paragraph(paragraph: str, style: str = "neutral") -> dict:
    if not paragraph:
        return {"original": paragraph, "revised": paragraph, "status": "error", "chain": "none", "human_score": 0}

    chains = [
        {"name": "EN", "langs": ["en"]},
    ]
    if len(paragraph) < 50:
        chains.append({"name": "EN→CS→ES→IT→FR", "langs": ["en", "cs", "es", "it", "fr"]})

    best_result = None
    best_score = 0

    for chain in chains:
        logger.info(f"Testing chain {chain['name']} on paragraph: {paragraph[:50]}...")
        revised = translate_chunk(paragraph, chain["langs"])
        # Если перевод не удался (текст не изменился), применяем локальный синонимайзер
        if revised == paragraph or len(revised) == 0:
            logger.info(f"Chain {chain['name']} failed, applying local synonyms.")
            revised = apply_local_synonyms(paragraph)
            status = "partial"
            chain_name = "LOCAL"
        else:
            # Удаляем артефакты
            revised = re.sub(r'Vino quieren alejarte|Laboratorio, presupuesto|Empty Null: Final Drawings', '', revised)
            revised = re.sub(r'««««Ибис»»»»', '«Ибис»', revised)
            revised = revised.strip()
            if revised and len(revised) > 0:
                score = get_human_score(revised)
                logger.info(f"Chain {chain['name']} score: {score}")
                if score > best_score:
                    best_score = score
                    best_result = {
                        "original": paragraph,
                        "revised": revised,
                        "status": "done" if score > 50 else "partial",
                        "chain": chain["name"],
                        "human_score": score
                    }
                if score >= 70:
                    break
            else:
                # Если после очистки текст пуст, применяем синонимайзер
                revised = apply_local_synonyms(paragraph)
                status = "partial"
                chain_name = "LOCAL"

    if best_result:
        return best_result

    # Если ничего не сработало — применяем локальный синонимайзер
    revised = apply_local_synonyms(paragraph)
    return {
        "original": paragraph,
        "revised": revised,
        "status": "partial",
        "chain": "LOCAL",
        "human_score": get_human_score(revised)
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
    logger.info("=== api_revise: START (v3.10.1) ===")
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
                yield _sse("progress", {"chars": 0, "estimated_total": total, "percent": 0, "log": f"Начинаем обработку {total} абзацев..."})

                results = []
                for idx, para in enumerate(paragraphs):
                    yield _sse("paragraph_start", {
                        "index": idx,
                        "original": para,
                        "status": "processing"
                    })

                    try:
                        result = process_paragraph(para, style)
                    except Exception as e:
                        logger.error(f"Paragraph {idx} processing error: {e}")
                        result = {
                            "original": para,
                            "revised": para,
                            "status": "error",
                            "chain": "none",
                            "human_score": 0
                        }
                    results.append(result)

                    yield _sse("paragraph_status", {
                        "index": idx,
                        "original": result["original"],
                        "revised": result["revised"],
                        "status": result["status"],
                        "chain": result["chain"],
                        "human_score": result.get("human_score", 0)
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
                    "summary": f"Обработано {total} абзацев. Успешно: {status_counts['done']}, частично: {status_counts['partial']}, ошибок: {status_counts['error']}. Средний HUMAN: {avg_score}%",
                    "paragraphs": results,
                    "average_human_score": avg_score,
                    "overall_analysis": overall,
                    "status_counts": status_counts,
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
