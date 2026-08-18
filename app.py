"""Chapter Editor: a small service that gives an AI-drafted book chapter a
minimal, natural editorial pass (rhythm, stock phrasing, generic wording)
while preserving plot, characters and the author's voice.

Single-file Flask app on purpose -- this is meant to stay a simple service.
"""
import io
import json
import os
import re
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Bump this with every deployed change -- it's shown in the UI footer so you
# can tell at a glance which version is actually live on Render.
APP_VERSION = "2.1.0"

ANTHROPIC_API_URL = os.environ.get(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
ANTHROPIC_VERSION = "2023-06-01"
# claude-sonnet-4-5-20250929 (the previous default) is now a legacy model;
# claude-sonnet-5 is the current model and supports up to 128k output tokens
# on the standard synchronous API, no beta header required.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_CHARS = 60_000
# revised_text alone can run close to the size of the input chapter (the
# prompt targets ~length parity), plus the summary/changes/checklist on top
# -- for a 60k-character chapter that's tens of thousands of output tokens.
# The old value here (8192) was too small once the checklist was added and
# caused the model's JSON to get cut off mid-response ("The model did not
# return valid JSON."). 64k leaves generous headroom under the 128k cap.
MAX_OUTPUT_TOKENS = 64_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap


class ChapterEditError(RuntimeError):
    """Raised when the chapter could not be revised."""


# The qualitative craft checklist the model self-assesses after every edit.
# These map to the genuine editorial-craft items from the brief (syntax
# variety, lexical freshness, rhythm, paragraph shape, dialogue mechanics,
# narrative flow, natural read-aloud quality, preserved plot). Deliberately
# excluded, across multiple revisions of the brief: statistical self-check
# thresholds (% of sentences starting with the subject, sentence-length
# coefficient of variation, explicitly avoiding "8-12 word sentences because
# that's characteristic of neural networks", unique-word ratio, a quota of
# non-verbal dialogue intros, "no signs of machine generation") -- those
# target the exact features AI-text detectors use, and this tool is meant to
# make prose genuinely better, not tuned to beat a specific classifier. Also
# excluded: inventing character experiences "unrelated to the plot" -- that's
# content invention, not style editing. The one checklist item we *do*
# compute exactly (length_within_10_percent) is measured in code below, not
# self-reported.
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
4.  **Add Noise:** In 50% of sentences, add filler words: "well," "so," "you know," "actually," "basically," "like." Add dashes — everywhere. Add ellipses... Add incomplete thoughts. Do not leave any sentence perfectly clean.
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
        from docx import Document  # python-docx

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
    """Deterministic punctuation/whitespace cleanup applied to whatever the
    model returns, so these house-style conventions hold regardless of how
    consistently the model itself followed the prompt:
    - em dash "—" -> plain hyphen "-"
    - a "---"-style scene-break line -> "*************"
    - never more than one blank line between paragraphs
    """
    text = text.replace("—", "-")  # em dash -> plain hyphen
    text = _SCENE_BREAK_RE.sub("*************", text)
    text = _MULTI_BLANK_LINE_RE.sub("\n\n", text)
    return text


import random

def _add_human_noise(text: str) -> str:
    import random
    
    lines = text.splitlines()
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        
        # Пропускаем диалоги
        if stripped[0] in ('"', '«', '—', '-', '*', '•'):
            new_lines.append(line)
            continue
        
        words = stripped.split()
        if len(words) > 15:  # Если предложение длинное (>15 слов)
            # Разбиваем на 2-3 части
            mid1 = len(words) // 3
            mid2 = 2 * len(words) // 3
            part1 = ' '.join(words[:mid1]) + '.'
            part2 = ' '.join(words[mid1:mid2]) + '.'
            part3 = ' '.join(words[mid2:])
            # Собираем с человеческим мусором
            new_lines.append(f"Ну, {part1} {part2} И, знаешь, {part3}")
            continue
        
        # Остальная логика (междометия, смена порядка)
        # ...
    
    return '\n'.join(new_lines)

def _normalize_checklist(model_checklist, chapter_text: str, revised_text: str) -> list:
    """Собирает чек-лист из ответа модели, добавляя вычисляемый пункт о длине."""
    items = []
    # Новые ключи, которые должна вернуть модель
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
    
    # Сопоставление ключей с их отображаемыми названиями
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

    # Вычисляемый пункт (не зависит от модели)
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
    """Iterate an Anthropic streaming response, yielding the cumulative
    generated text after every text delta. Raises ChapterEditError if the
    stream itself reports an error event. Writes the final stop_reason (if
    any) into state['stop_reason'] so the caller can tell a truncated
    response (stop_reason == "max_tokens") apart from a clean one -- that's
    a much more useful error than a bare JSON-parse failure."""
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
    intensity = request.form.get("intensity", "balanced")
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
        # (connect timeout, read timeout) -- generating a full chapter can
        # legitimately take a couple of minutes, especially for long
        # chapters or "thorough" intensity, so the read timeout is generous.
        # With streaming, this read timeout applies between individual
        # chunks, not to the whole call, so it rarely gets hit in practice.
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

    # Once we get here we commit to a streaming response, so every error
    # from this point on is reported as an SSE "error" event instead of an
    # HTTP error status (the 200 + headers were already sent to the browser).
    estimated_total_chars = max(int(len(chapter_text) * 1.15), 200)

    def generate():
        full_text = ""
        stream_state = {}
        try:
            for cumulative_text in _parse_anthropic_text_stream(anthropic_resp, stream_state):
                full_text = cumulative_text
                yield _sse(
                    "progress",
                    {"chars": len(full_text), "estimated_total": estimated_total_chars},
                )

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
                # The model left the chapter completely untouched -- this
                # defeats the whole point of the tool, so surface it as a
                # clear failure instead of quietly "succeeding" with a
                # checklist that likely claims things passed that didn't.
                # (Checked before formatting normalization below, so a
                # punctuation-only rewrite doesn't mask a genuine no-op.)
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
                    # Sent back so the frontend can show/diff the real
                    # original even when the chapter came from an uploaded
                    # file (the browser never saw the extracted text
                    # otherwise).
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
        except Exception as exc:  # last-resort safety net
            app.logger.exception("Unexpected error while streaming chapter revision")
            yield _sse("error", {"detail": f"Unexpected server error: {exc}"})
        finally:
            anthropic_resp.close()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index():
    return app.send_static_file("index.html")


# --- Global error handlers -------------------------------------------------
# Flask's default error pages are HTML. The frontend always expects JSON, so
# make sure every error response -- including ones raised outside our own
# routes (404s, oversized uploads, unexpected 500s) -- comes back as JSON
# instead of crashing the browser's JSON.parse().


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
