"""Chapter Editor: a small service that gives an AI-drafted book chapter a
minimal, natural editorial pass (rhythm, stock phrasing, generic wording)
while preserving plot, characters and the author's voice.

Single-file Flask app on purpose -- this is meant to stay a simple service.
"""
import io
import json
import os
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Bump this with every deployed change -- it's shown in the UI footer so you
# can tell at a glance which version is actually live on Render.
APP_VERSION = "1.3.0"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
MAX_CHARS = 60_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap


class ChapterEditError(RuntimeError):
    """Raised when the chapter could not be revised."""


SYSTEM_PROMPT = """You are a meticulous literary editor who specializes in \
polishing chapters that were drafted with AI assistance. Your job is line \
editing, not rewriting: keep the plot, characters, facts, dialogue meaning, \
and the author's voice completely intact.

The goal is prose that reads like it came from an attentive human author \
with a distinct voice. Judge yourself by how the passage actually sounds \
and feels when read -- NOT by matching any statistical profile (sentence-\
length variance, percentage of sentences starting with the subject, \
unique-word ratio, or similar metrics). Do not treat this as a checklist \
to satisfy; treat it as craft.

Working moves, use only what genuinely improves a given passage:
- Syntax: vary sentence construction -- inversions, parenthetical asides, \
an occasional rhetorical question or interjection. Break up long, uniform \
sentences; join short choppy ones with conjunctions where it helps the \
rhythm. Not every sentence needs to be plain subject-verb-object.
- Word choice: cut clichéd, bureaucratic, or overly bookish phrasing (stock \
words like "process," "situation," "ultimately," "accordingly," and their \
equivalents in the chapter's own language) for concrete, vivid, colloquial \
alternatives. Bring in a fresh comparison or image where it fits naturally \
-- don't force one into every paragraph.
- Interiority: where a character is already present in a scene, you may \
surface a sensory detail or reaction that's implied but left flat in the \
draft (a sound, a smell, a flicker of feeling) -- but stay anchored to what \
the scene already supports. Do not invent new plot-relevant experiences, \
opinions, or events that aren't implied by the original.
- Rhythm: alternate short, punchy sentences with longer, flowing ones. Use \
dashes, colons, and ellipses where a human writer would reach for them -- \
including, occasionally, a deliberately unfinished thought.
- Paragraph shape: don't open every paragraph with the grammatical subject \
-- lead with a setting, a gesture, a participial phrase sometimes. Let \
transitions between sentences be a little less tidy than a textbook \
outline; real prose doesn't march in perfect logical lockstep.
- Final pass: read it back mentally -- does it sound like a person telling \
the story, or like a summary of one? Trim leftover over-smoothness; a touch \
of repetition, a colloquial particle, a rough edge here and there reads \
more human than uniform polish.

Make the *minimum* number of changes needed -- this is a polish pass, not a \
rewrite. Preserve paragraph breaks, chapter structure, character names, and \
factual continuity exactly. Do not add new plot events. Do not change the \
language the chapter is written in, and do not translate it. Keep the total \
length within roughly ±10% of the original.

Respond with a single JSON object and nothing else, matching this schema:
{"revised_text": string, "summary": string, "changes": [string, ...]}

- revised_text: the full edited chapter, ready to use as-is
- summary: 1-3 sentences describing the overall edit, written in the \
chapter's own language
- changes: 3-8 short notes (in the chapter's own language) describing the \
kinds of edits made, e.g. "shortened three overly uniform sentences in the \
second scene" or "opened a paragraph with a gesture instead of the subject"
"""

INTENSITY_HINTS = {
    "light": (
        "Make a light-touch pass: fix only the most obvious mechanical "
        "tics, and change as little as possible otherwise."
    ),
    "balanced": (
        "Make a normal editorial pass: noticeable but restrained "
        "improvements throughout the chapter -- touch syntax, word choice, "
        "rhythm and paragraph openings where they clearly help."
    ),
    "thorough": (
        "Make a thorough line-edit pass while still respecting every "
        "constraint above -- more sentences may be touched, rhythm and "
        "paragraph shape may change more freely, but do not rewrite whole "
        "scenes, invent content, or add anything not implied by the "
        "original."
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


def revise_chapter(chapter_text: str, intensity: str = "balanced") -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ChapterEditError(
            "ANTHROPIC_API_KEY is not set on the server. Add it in your "
            "environment (locally via .env, on Render via the service's "
            "Environment settings)."
        )

    hint = INTENSITY_HINTS.get(intensity, INTENSITY_HINTS["balanced"])
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8192,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"{hint}\n\n---\nCHAPTER TEXT:\n---\n{chapter_text}",
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
        resp = requests.post(
            ANTHROPIC_API_URL, headers=headers, json=payload, timeout=(10, 300)
        )
    except requests.exceptions.ReadTimeout as exc:
        raise ChapterEditError(
            "The Anthropic API took too long to respond (over 5 minutes). "
            "Try a shorter chapter or a lighter intensity, then try again."
        ) from exc
    except requests.RequestException as exc:
        raise ChapterEditError(f"Network error calling the Anthropic API: {exc}") from exc

    if resp.status_code != 200:
        raise ChapterEditError(
            f"Anthropic API error ({resp.status_code}): {resp.text[:500]}"
        )

    data = resp.json()
    raw = "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise ChapterEditError("The model did not return valid JSON.")
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ChapterEditError("The model did not return valid JSON.") from exc

    if not parsed.get("revised_text"):
        raise ChapterEditError("The model response was missing the revised text.")

    return {
        "revised_text": parsed["revised_text"],
        "summary": parsed.get("summary", ""),
        "changes": parsed.get("changes", []),
    }


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    file_storage = request.files.get("file")
    text = request.form.get("text", "")
    intensity = request.form.get("intensity", "balanced")

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

    try:
        result = revise_chapter(chapter_text, intensity=intensity)
    except ChapterEditError as exc:
        return jsonify(detail=str(exc)), 502
    except Exception as exc:  # last-resort safety net -- never let a raw
        # traceback / HTML page reach the browser, always return JSON.
        app.logger.exception("Unexpected error in /api/revise")
        return jsonify(detail=f"Unexpected server error: {exc}"), 500

    return jsonify(result)


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
