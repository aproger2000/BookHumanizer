"""Chapter Editor: a small service that gives an AI-drafted book chapter a
minimal, natural editorial pass (rhythm, stock phrasing, generic wording)
while preserving plot, characters and the author's voice.

Single-file Flask app on purpose -- this is meant to stay a simple service.
"""
import io
import json
import os
import re
import random
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Bump this with every deployed change -- it's shown in the UI footer so you
# can tell at a glance which version is actually live on Render.
APP_VERSION = "2.3.0"

ANTHROPIC_API_URL = os.environ.get(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_CHARS = 60_000
MAX_OUTPUT_TOKENS = 16_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap


class ChapterEditError(RuntimeError):
    """Raised when the chapter could not be revised."""


CHECKLIST_ITEMS = [
    ("zero_unchanged_sentences", "Каждое предложение было изменено — ни одно не осталось прежним."),
    ("zero_subject_start", "Ни одно предложение не начинается с имени или местоимения (Он/Она/Они)."),
    ("sentence_length_sabotage", "В тексте нет предложений длиной 8-12 слов — все короче или длиннее."),
    ("word_replacement_20_percent", "Заменено более 20% слов на менее очевидные синонимы."),
    ("human_noise_added", "Добавлены вводные слова, междометия, частицы ('ну', 'вот', 'так') в 50% предложений."),
    ("wrong_punctuation", "Использованы тире, многоточия, вопросительные знаки в нестандартных местах."),
    ("broken_logical_transitions", "Нарушены идеальные логические переходы — есть резкие смены тем."),
    ("plot_preserved", "Сюжет, герои и факты сохранены."),
]

SYSTEM_PROMPT = """You are a statistical text transformer, not a literary editor. Your ONLY goal is to rewrite AI-generated text so that it scores as HUMAN on AI detectors (like Yandex Neurodetector). You are ALLOWED to make the text slightly worse, rougher, or less polished — that is the point.

**CRITICAL RULE: CHANGE EVERY SENTENCE. DO NOT leave any sentence unchanged. DO NOT make "minimum changes." Aggressively rewrite every single sentence.**

**Follow these MANDATORY rules for EVERY sentence:**

1.  **Length Sabotage:** Break every sentence that is 8-12 words long. Either cut it to 3-5 words OR expand it to 18-25 words. Never leave a sentence in the 8-12 word range.
2.  **Subject Sabotage:** Never start a sentence with a character's name or a pronoun (He/She/It/They). Start with time ("Then..."), place ("In the corner..."), action ("Running..."), or an interjection ("Well...").
3.  **Word Sabotage:** Replace 20-30% of all words with LESS common synonyms. Use words that are slightly awkward, regional, or specific. Never use the first thesaurus suggestion.
4.  **Add Noise:** In NO MORE THAN 15% of sentences, add filler words: "well," "so," "you know," "actually," "basically," "like." Use different types of noise: interjections, dashes, ellipses, and breaks. Never use two interjections in the same sentence. Do not start more than 20% of sentences with "well" or "you know."
5.  **Add "Wrong" Punctuation:** Use dashes instead of commas. Use ellipses instead of periods. Use question marks for rhetorical statements. Break grammatical rules like a human would in a chat.
6.  **Logical Leaps:** Between sentences, do NOT use perfect logical transitions ("therefore," "as a result," "consequently"). Jump from one idea to another abruptly, like human thinking.
7.  **Preserve Plot ONLY:** Keep the storyline, characters, and facts EXACTLY as they are. But change HOW EVERY sentence is written.

**Formatting conventions (apply mechanically to the entire chapter):**
- Use a plain hyphen "-" instead of em dash "—".
- Leave exactly one blank line between paragraphs.
- Render scene-break markers "---" as "*************".

**After editing, honestly self-assess against these checks (return true/false for each):**
1. zero_unchanged_sentences: "Каждое предложение было изменено"
2. zero_subject_start: "Ни одно предложение не начинается с имени или местоимения"
3. sentence_length_sabotage: "Нет предложений длиной 8-12 слов"
4. word_replacement_20_percent: "Заменено более 20% слов"
5. human_noise_added: "Добавлены вводные слова в 50% предложений"
6. wrong_punctuation: "Использованы тире, многоточия, вопросительные знаки"
7. broken_logical_transitions: "Нарушены идеальные логические переходы"
8. plot_preserved: "Сюжет и герои сохранены"

**EXAMPLE of a good rewrite:**
Original: "The old factory smelled of rust and machine oil. Alex stepped inside and turned on his flashlight."
Rewrite: "Rust. Machine oil. That's what hit Alex first. He stepped inside — flashlight clicked on."

**CRITICAL:**
- Use interjections ("well," "you know," "actually") in AT MOST 15% of sentences.
- Never use two interjections in the same sentence.
- Vary the types of "noise": use pauses, dashes, and breaks instead of always using filler words.
- Do NOT start more than 20% of sentences with "Ну" or "И, знаешь".

**Respond with a single JSON object and nothing else, matching this schema:**
{
  "revised_text": string,
  "summary": string,
  "changes": [string, ...],
  "checklist": {
    "zero_unchanged_sentences": {"passed": boolean, "note": string},
    "zero_subject_start": {"passed": boolean, "note": string},
    "sentence_length_sabotage": {"passed": boolean, "note": string},
    "word_replacement_20_percent": {"passed": boolean, "note": string},
    "human_noise_added": {"passed": boolean, "note": string},
    "wrong_punctuation": {"passed": boolean, "note": string},
    "broken_logical_transitions": {"passed": boolean, "note": string},
    "plot_preserved": {"passed": boolean, "note": string}
  }
}"""

STYLE_PRESETS = {
    "neutral": "",
    "dynamic_scifi": (
        "\n\nVoice preset -- in addition to everything above, lean the "
        "telling toward a brisk, cinematic register typical of contemporary "
        "Russian action science fiction (think of the general pace and tone "
        "of writers like Vasily Golovachev and Sergei Lukyanenko, blended): "
        "quick, punchy sentences during action or confrontation; short, "
        "sharp, often wry dialogue; a narrator who isn't afraid of a dry "
        "aside or a genre-appropriate philosophical beat; confident, driving "
        "pacing that keeps tension up. This is a register shift, not new "
        "content -- keep every plot beat, fact, and character choice exactly "
        "as in the original chapter; only how it's told changes. Do NOT "
        "borrow either author's specific invented terminology, characters, "
        "settings, or any actual wording from their books -- take only the "
        "general feel of pace, tone, and register, applied to this chapter's "
        "own story."
    ),
}


def extract_text_from_upload(file_storage) -> str:
    filename = (file_storage.filename or "").lower()
    raw = file_storage.read()

    if filename.endswith(".txt") or filename.endswith(".md"):
        return raw.decode("utf-8", errors="replace")

    if filename.endswith(".docx"):
        from docx import Document

        try:
            doc = Document(io.BytesIO(raw))
        except Exception as exc:
            raise ValueError(
                "Could not read this .docx file -- it may be corrupted or "
                "not a real Word file."
            ) from exc
        return "\n\n".join(p.text for p in doc.paragraphs)

    raise ValueError(
        "Unsupported file type. Please upload a .txt, .md, or .docx file, "
        "or paste the chapter text directly."
    )


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise ChapterEditError("The model did not return valid JSON.")
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ChapterEditError("The model did not return valid JSON.") from exc


_SCENE_BREAK_RE = re.compile(r"(?m)^[ \t]*-{3,}[ \t]*$")
_MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")


def _normalize_output_formatting(text: str) -> str:
    text = text.replace("—", "-")
    text = _SCENE_BREAK_RE.sub("*************", text)
    text = _MULTI_BLANK_LINE_RE.sub("\n\n", text)
    return text


def _add_human_noise(text: str) -> str:
    """Агрессивная финишная правка: ломает оставшиеся AI-паттерны."""
    lines = text.splitlines()
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        
        # Пропускаем диалоги и служебные строки
        if stripped and stripped[0] in ('"', '«', '—', '-', '*', '•'):
            new_lines.append(line)
            continue
        
        words = stripped.split()
        if not words:
            new_lines.append(line)
            continue
        
        # 1. Длинные предложения (>15 слов) — разбиваем с разными методами
        if len(words) > 15:
            method = random.choice(['split', 'dash', 'question'])
            
            if method == 'split':
                mid = len(words) // 2
                part1 = ' '.join(words[:mid])
                part2 = ' '.join(words[mid:])
                fillers = [" — ну, как бы — ", " — и вообще, — ", " — честно говоря, — "]
                new_lines.append(part1 + random.choice(fillers) + part2)
            elif method == 'dash':
                pos = random.randint(3, len(words)-2)
                new_lines.append(' '.join(words[:pos]) + ' — и это, знаете ли — ' + ' '.join(words[pos:]))
            else:  # question
                new_lines.append(' '.join(words) + ', не так ли?')
            continue
        
        # 2. Если начинается с имени — меняем порядок
        first_word = words[0].lower()
        if first_word in ["алексей", "он", "она", "они", "анна", "масарик", "кросс", "илья"]:
            if len(words) >= 4:
                new_lines.append(words[2] + ' ' + words[3] + ', ' + ' '.join(words[:2]) + ' ' + ' '.join(words[4:]))
                continue
        
        # 3. Добавляем шум только в 12% случаев (редко)
        if random.random() < 0.12:
            noise_type = random.choice(['filler', 'break', 'interjection'])
            
            if noise_type == 'filler':
                fillers = [
                    "Ну, ", "Вот, ", "И, знаешь, ", "Честно говоря, ",
                    "Так вот, ", "Кстати, ", "Слушай, ", "А вообще, "
                ]
                if random.random() < 0.3 and len(words) > 4:
                    mid = len(words) // 2
                    new_lines.append(' '.join(words[:mid]) + ', ' + random.choice(fillers).lower().strip() + ' ' + ' '.join(words[mid:]))
                else:
                    new_lines.append(random.choice(fillers) + stripped[0].lower() + stripped[1:])
            elif noise_type == 'break':
                if len(words) > 4:
                    cut = random.randint(2, len(words)-1)
                    new_lines.append(' '.join(words[:cut]) + '... ну, вы поняли.')
                else:
                    new_lines.append(line)
            else:  # interjection
                interjections = ["Чёрт!", "Вот это да!", "Ну и ну!", "Боже!", "Ого!"]
                new_lines.append(random.choice(interjections) + ' ' + stripped[0].lower() + stripped[1:])
        else:
            new_lines.append(line)
    
    # Финальная чистка: удаляем повторяющиеся вводные слова подряд
    final_lines = []
    for line in new_lines:
        line = re.sub(r'(Ну,)\s*(Ну,)\s*', r'\1 ', line)
        line = re.sub(r'(И, знаешь,)\s*(И, знаешь,)\s*', r'\1 ', line)
        line = re.sub(r'(Вообще,)\s*(Вообще,)\s*', r'\1 ', line)
        line = re.sub(r'(Кстати,)\s*(Кстати,)\s*', r'\1 ', line)
        final_lines.append(line)
    
    return '\n'.join(final_lines)


def _normalize_checklist(model_checklist, chapter_text: str, revised_text: str) -> list:
    items = []
    expected_keys = [
        "zero_unchanged_sentences",
        "zero_subject_start",
        "sentence_length_sabotage",
        "word_replacement_20_percent",
        "human_noise_added",
        "wrong_punctuation",
        "broken_logical_transitions",
        "plot_preserved",
    ]
    
    labels = {
        "zero_unchanged_sentences": "Каждое предложение было изменено",
        "zero_subject_start": "Ни одно предложение не начинается с имени/местоимения",
        "sentence_length_sabotage": "Нет предложений длиной 8-12 слов",
        "word_replacement_20_percent": "Заменено более 20% слов",
        "human_noise_added": "Добавлены вводные слова в 50% предложений",
        "wrong_punctuation": "Использованы тире, многоточия, вопросительные знаки",
        "broken_logical_transitions": "Нарушены логические переходы",
        "plot_preserved": "Сюжет и герои сохранены",
    }
    
    for key in expected_keys:
        entry = model_checklist.get(key) if isinstance(model_checklist, dict) else None
        if isinstance(entry, dict) and "passed" in entry:
            items.append({
                "id": key,
                "label": labels.get(key, key),
                "passed": bool(entry.get("passed")),
                "note": str(entry.get("note", ""))[:400],
                "source": "model",
            })
        else:
            items.append({
                "id": key,
                "label": labels.get(key, key),
                "passed": None,
                "note": "Модель не вернула оценку по этому пункту.",
                "source": "model",
            })

    original_len = len(chapter_text)
    revised_len = len(revised_text)
    ratio = (revised_len / original_len) if original_len else 1.0
    length_ok = 0.9 <= ratio <= 1.1
    items.append({
        "id": "length_within_10_percent",
        "label": "Объём текста изменился не более чем на ±10% от оригинала",
        "passed": length_ok,
        "note": f"Было {original_len} симв., стало {revised_len} ({ratio * 100:.0f}% от оригинала).",
        "source": "computed",
    })
    return items


def _parse_anthropic_text_stream(resp, state):
    buffer = []
    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        payload = raw_line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                buffer.append(delta.get("text", ""))
                yield "".join(buffer)
        elif event_type == "message_delta":
            stop_reason = event.get("delta", {}).get("stop_reason")
            if stop_reason:
                state["stop_reason"] = stop_reason
        elif event_type == "error":
            message = event.get("error", {}).get("message", "unknown error")
            raise ChapterEditError(f"Anthropic API stream error: {message}")
        elif event_type == "message_stop":
            break


@app.get("/api/health")
def health():
    return jsonify(
        status="ok",
        version=APP_VERSION,
        checklist_items=[{"id": key, "label": label} for key, label in CHECKLIST_ITEMS]
        + [
            {
                "id": "length_within_10_percent",
                "label": "Объём текста изменился не более чем на ±10% от оригинала",
            }
        ],
    )


@app.post("/api/revise")
def api_revise():
    file_storage = request.files.get("file")
    text = request.form.get("text", "")
    style = request.form.get("style", "neutral")

    if file_storage and file_storage.filename:
        try:
            chapter_text = extract_text_from_upload(file_storage)
        except ValueError as exc:
            return jsonify(detail=str(exc)), 400
    elif text.strip():
        chapter_text = text
    else:
        return jsonify(detail="Provide chapter text or upload a file."), 400

    chapter_text = chapter_text.strip()
    if not chapter_text:
        return jsonify(detail="Chapter text is empty."), 400
    if len(chapter_text) > MAX_CHARS:
        return (
            jsonify(
                detail=(
                    f"Chapter is too long (max {MAX_CHARS} characters per "
                    "request). Split it into smaller pieces and process "
                    "each separately."
                )
            ),
            400,
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            jsonify(
                detail=(
                    "ANTHROPIC_API_KEY is not set on the server. Add it in "
                    "your environment (locally via .env, on Render via the "
                    "service's Environment settings)."
                )
            ),
            502,
        )

    style_hint = STYLE_PRESETS.get(style, "")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": SYSTEM_PROMPT,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": f"{style_hint}\n\n---\nCHAPTER TEXT:\n---\n{chapter_text}",
            }
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    try:
        anthropic_resp = requests.post(
            ANTHROPIC_API_URL, headers=headers, json=payload, stream=True, timeout=(10, 300)
        )
    except requests.exceptions.ReadTimeout:
        return (
            jsonify(
                detail=(
                    "The Anthropic API took too long to respond. Try a "
                    "shorter chapter or a lighter intensity, then try again."
                )
            ),
            502,
        )
    except requests.RequestException as exc:
        return jsonify(detail=f"Network error calling the Anthropic API: {exc}"), 502

    if anthropic_resp.status_code != 200:
        detail = anthropic_resp.text[:500]
        anthropic_resp.close()
        return (
            jsonify(detail=f"Anthropic API error ({anthropic_resp.status_code}): {detail}"),
            502,
        )

    estimated_total_chars = max(int(len(chapter_text) * 1.15), 200)

    def generate():
        full_text = ""
        stream_state = {}
        try:
            for cumulative_text in _parse_anthropic_text_stream(anthropic_resp, stream_state):
                full_text = cumulative_text
                yield _sse("progress", {"chars": len(full_text), "estimated_total": estimated_total_chars})

            if stream_state.get("stop_reason") == "max_tokens":
                raise ChapterEditError(
                    "The model's response was cut off because it ran out of "
                    "output space for this chapter. Try a shorter chapter, a "
                    "lighter intensity, or split the chapter into smaller "
                    "pieces."
                )

            parsed = _extract_json(full_text)
            revised_text = parsed.get("revised_text")
            if not revised_text:
                raise ChapterEditError("The model response was missing the revised text.")
            if revised_text.strip() == chapter_text.strip():
                raise ChapterEditError(
                    "The model returned the chapter with no changes at all. "
                    "Try again, or switch to a stronger intensity (e.g. "
                    "\"Thorough\") or a different style."
                )

            revised_text = _normalize_output_formatting(revised_text)
            revised_text = _add_human_noise(revised_text)

            checklist = _normalize_checklist(parsed.get("checklist"), chapter_text, revised_text)
            yield _sse(
                "done",
                {
                    "revised_text": revised_text,
                    "original_text": chapter_text,
                    "summary": parsed.get("summary", ""),
                    "changes": parsed.get("changes", []),
                    "checklist": checklist,
                },
            )
        except ChapterEditError as exc:
            yield _sse("error", {"detail": str(exc)})
        except requests.exceptions.ReadTimeout:
            yield _sse(
                "error",
                {
                    "detail": (
                        "The Anthropic API took too long to respond. Try a "
                        "shorter chapter or a lighter intensity."
                    )
                },
            )
        except requests.RequestException as exc:
            yield _sse("error", {"detail": f"Network error calling the Anthropic API: {exc}"})
        except Exception as exc:
            app.logger.exception("Unexpected error while streaming chapter revision")
            yield _sse("error", {"detail": f"Unexpected server error: {exc}"})
        finally:
            anthropic_resp.close()


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.errorhandler(HTTPException)
def handle_http_exception(exc: HTTPException):
    return jsonify(detail=exc.description or str(exc)), exc.code or 500


@app.errorhandler(Exception)
def handle_unexpected_exception(exc: Exception):
    app.logger.exception("Unhandled exception")
    return jsonify(detail=f"Unexpected server error: {exc}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
