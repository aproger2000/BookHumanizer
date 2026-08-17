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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
MAX_CHARS = 60_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")


class ChapterEditError(RuntimeError):
    """Raised when the chapter could not be revised."""


SYSTEM_PROMPT = """You are a meticulous literary editor who specializes in \
polishing chapters that were drafted with AI assistance. Your job is line \
editing, not rewriting: keep the plot, characters, facts, dialogue meaning, \
and the author's voice completely intact.

Focus on removing the small tics that make AI-assisted prose feel \
mechanical, and give it a distinct, natural human cadence instead:
- vary sentence length and structure instead of a uniform rhythm
- cut stock transitional phrases ("moreover", "furthermore", "in conclusion", \
"it is worth noting", "additionally", and their equivalents in the chapter's \
own language) and generic hedging
- replace generic, abstract phrasing with concrete, specific, sensory detail \
where it fits the scene
- break up repetitive sentence openers and repeated grammatical patterns
- avoid listy, over-structured paragraphs; let the prose breathe the way a \
person telling a story would
- trim redundant restatements and throat-clearing
- keep idiosyncrasies: the occasional fragment, an unusual word choice, an \
imperfect but natural rhythm

Make the *minimum* number of changes needed to achieve this -- this is a \
polish pass, not a rewrite. Preserve paragraph breaks, chapter structure, \
character names, and factual continuity exactly. Do not add new plot \
events or invent details. Do not change the language the chapter is \
written in, and do not translate it.

Respond with a single JSON object and nothing else, matching this schema:
{"revised_text": string, "summary": string, "changes": [string, ...]}

- revised_text: the full edited chapter, ready to use as-is
- summary: 1-3 sentences describing the overall edit, written in the \
chapter's own language
- changes: 3-8 short notes (in the chapter's own language) describing the \
kinds of edits made, e.g. "shortened three overly uniform sentences in the \
second scene" or "removed repeated use of a stock transition word"
"""

INTENSITY_HINTS = {
    "light": (
        "Make a light-touch pass: fix only the most obvious mechanical "
        "tics, and change as little as possible otherwise."
    ),
    "balanced": (
        "Make a normal editorial pass: noticeable but restrained "
        "improvements throughout the chapter."
    ),
    "thorough": (
        "Make a thorough line-edit pass while still respecting every "
        "constraint above -- more sentences may be touched, but do not "
        "rewrite whole scenes or add content."
    ),
}


def extract_text_from_upload(file_storage) -> str:
    filename = (file_storage.filename or "").lower()
    raw = file_storage.read()

    if filename.endswith(".txt") or filename.endswith(".md"):
        return raw.decode("utf-8", errors="replace")

    if filename.endswith(".docx"):
        from docx import Document  # python-docx

        doc = Document(io.BytesIO(raw))
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
    return jsonify(status="ok")


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

    return jsonify(result)


@app.get("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
