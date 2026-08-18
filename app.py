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
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Bump this with every deployed change -- it's shown in the UI footer so you
# can tell at a glance which version is actually live on Render.
APP_VERSION = "1.9.1"

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
    (
        "syntax_variety",
        "Синтаксис разнообразен: есть инверсии и вставные конструкции, "
        "не всё построено по схеме «подлежащее — сказуемое»",
    ),
    (
        "sentence_length_mix",
        "Длинные предложения разбиты там, где это шло на пользу; короткие "
        "при необходимости объединены союзами",
    ),
    (
        "cliches_removed",
        "Штампы и канцелярские обороты заменены на живые, конкретные "
        "формулировки",
    ),
    (
        "fresh_imagery",
        "Есть уместные образные сравнения или метафоры, без натяжек",
    ),
    (
        "uneven_rhythm",
        "Ритм заметно неровный: длинные и короткие предложения ощутимо "
        "чередуются, нет длинных цепочек фраз одинаковой длины, встречаются "
        "тире и многоточия",
    ),
    (
        "paragraph_openings",
        "Не все абзацы начинаются с подлежащего",
    ),
    (
        "natural_transitions",
        "Переходы между предложениями не выглядят механически ровными",
    ),
    (
        "reads_naturally_aloud",
        "Текст звучит естественно при чтении вслух, без излишней «гладкости»",
    ),
    (
        "plot_preserved",
        "Сюжет, факты и герои не изменились; ничего лишнего не придумано",
    ),
    (
        "dialogue_natural",
        "Диалоги (если есть) звучат живо: не только «сказал/ответил», "
        "есть паузы, действия и обрывы реплик между ними",
    ),
    (
        "no_storyboard_sequencing",
        "Повествование не выглядит как раскадровка «сделал это — потом "
        "то — затем вот это»",
    ),
    (
        "full_coverage",
        "Правка затронула все абзацы без исключения — ни один не "
        "оставлен точно в исходном виде",
    ),
]

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
alternatives. Where the draft reaches for an ornate or overly "literary" \
synonym and a simpler, more natural word would read better in context, use \
the simpler one. If a comparison or image is a worn-out cliché, replace it \
with something more specific and fitting to this scene -- don't just leave \
it or swap it for another generic one. Bring in a fresh comparison or image \
where it fits naturally -- don't force one into every paragraph.
- Interiority: where a character is already present in a scene, you may \
surface a sensory detail or reaction that's implied but left flat in the \
draft (a sound, a smell, a flicker of feeling) -- but stay anchored to what \
the scene already supports. Do not invent new plot-relevant experiences, \
opinions, or events that aren't implied by the original.
- Rhythm: make sentence length swing noticeably -- a longer, flowing \
sentence, then something short and blunt, maybe another short one, before \
flowing out again. Don't let three or more sentences in a row land at \
roughly the same length and shape; break that pattern up. Use dashes, \
colons, and ellipses where a human writer would reach for them -- \
including, occasionally, a deliberately unfinished thought.
- Paragraph shape: don't open every paragraph with the grammatical subject \
-- lead with a setting, a gesture, a participial phrase sometimes. Let \
transitions between sentences be a little less tidy than a textbook \
outline; real prose doesn't march in perfect logical lockstep.
- Dialogue: if the passage has dialogue, don't rely only on "he said" / \
"she answered." Use pauses, small actions, and gestures between lines \
instead of a verb every time. A line can trail off or get cut short where \
that fits the moment -- real conversation isn't always tidy and complete. \
Keep every character's actual words and meaning intact; only touch how the \
lines are introduced and paced.
- Narrative flow: avoid a mechanical "did this, then this, then this" \
blow-by-blow of actions. Let the narration's attention shift the way a \
person's would -- lingering on one detail, skipping past another -- rather \
than reporting every step in even, sequential order.
- Final pass: read it back mentally -- does it sound like a person telling \
the story, or like a summary of one? Trim leftover over-smoothness; a touch \
of repetition, a colloquial particle, a rough edge here and there reads \
more human than uniform polish.

Make the *minimum* number of changes needed -- this is a polish pass, not a \
rewrite. Preserve paragraph breaks, chapter structure, character names, and \
factual continuity exactly. Do not add new plot events. Do not change the \
language the chapter is written in, and do not translate it. Keep the total \
length within roughly ±10% of the original.

Coverage is a hard requirement, not a suggestion: every single paragraph \
in the chapter must come out different from how it went in -- zero \
exceptions, including short paragraphs, dialogue-only paragraphs, and \
paragraphs that already read fine. "Minimum changes" controls how much a \
given paragraph changes, never whether it changes. If a paragraph seems \
like it needs nothing, that's a sign to look harder -- swap one word, break \
one sentence, add one dash -- not a reason to leave it byte-for-byte as \
written. A revised chapter that is identical, or nearly identical, to the \
original anywhere is a failed edit.

After editing, honestly self-assess the revised text against these twelve \
checks (do not just mark everything true -- if something genuinely doesn't \
hold, say so; mark dialogue_natural as passed:true with a note saying so if \
the passage has no dialogue at all):
1. syntax_variety
2. sentence_length_mix
3. cliches_removed
4. fresh_imagery
5. uneven_rhythm
6. paragraph_openings
7. natural_transitions
8. reads_naturally_aloud
9. plot_preserved
10. dialogue_natural
11. no_storyboard_sequencing
12. full_coverage

Respond with a single JSON object and nothing else, matching this schema:
{
  "revised_text": string,
  "summary": string,
  "changes": [string, ...],
  "checklist": {
    "syntax_variety": {"passed": boolean, "note": string},
    "sentence_length_mix": {"passed": boolean, "note": string},
    "cliches_removed": {"passed": boolean, "note": string},
    "fresh_imagery": {"passed": boolean, "note": string},
    "uneven_rhythm": {"passed": boolean, "note": string},
    "paragraph_openings": {"passed": boolean, "note": string},
    "natural_transitions": {"passed": boolean, "note": string},
    "reads_naturally_aloud": {"passed": boolean, "note": string},
    "plot_preserved": {"passed": boolean, "note": string},
    "dialogue_natural": {"passed": boolean, "note": string},
    "no_storyboard_sequencing": {"passed": boolean, "note": string},
    "full_coverage": {"passed": boolean, "note": string}
  }
}

- revised_text: the full edited chapter, ready to use as-is
- summary: 1-3 sentences describing the overall edit, written in the \
chapter's own language
- changes: 3-8 short notes (in the chapter's own language) describing the \
kinds of edits made, e.g. "shortened three overly uniform sentences in the \
second scene" or "opened a paragraph with a gesture instead of the subject"
- checklist: your honest per-item verdict, with a one-sentence "note" in \
the chapter's own language explaining it
"""

INTENSITY_HINTS = {
    "light": (
        "Make a light-touch pass: every paragraph still gets touched, but "
        "keep each individual touch small -- fix only the most obvious "
        "mechanical tics per paragraph, nothing more. \"Light\" controls "
        "how much a paragraph changes, not whether it changes: do not skip "
        "a paragraph just because it already reads acceptably."
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

# Optional voice/register presets, layered on top of the craft moves above.
# "dynamic_scifi" nudges the telling toward the brisk, cinematic register
# common to Russian action-sci-fi (writers like Vasily Golovachev and Sergei
# Lukyanenko are a useful reference point) -- pace, tone, dialogue rhythm.
# General prose style isn't copyrightable and this only ever touches the
# user's own chapter text, but the instruction explicitly forbids borrowing
# either author's actual invented terminology, characters, or wording, so
# this stays a register shift and never turns into reproducing or
# imitating anyone's specific protected fictional universe.
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


def _normalize_checklist(model_checklist, chapter_text: str, revised_text: str) -> list:
    """Merge the model's self-reported checklist with one item we compute
    exactly ourselves (length ratio), always in a fixed, known order."""
    items = []
    for key, label in CHECKLIST_ITEMS:
        entry = model_checklist.get(key) if isinstance(model_checklist, dict) else None
        if isinstance(entry, dict) and "passed" in entry:
            items.append(
                {
                    "id": key,
                    "label": label,
                    "passed": bool(entry.get("passed")),
                    "note": str(entry.get("note", ""))[:400],
                    "source": "model",
                }
            )
        else:
            items.append(
                {
                    "id": key,
                    "label": label,
                    "passed": None,
                    "note": "Модель не вернула оценку по этому пункту.",
                    "source": "model",
                }
            )

    original_len = len(chapter_text)
    revised_len = len(revised_text)
    ratio = (revised_len / original_len) if original_len else 1.0
    length_ok = 0.9 <= ratio <= 1.1
    items.append(
        {
            "id": "length_within_10_percent",
            "label": "Объём текста изменился не более чем на ±10% от оригинала",
            "passed": length_ok,
            "note": (
                f"Было {original_len} симв., стало {revised_len} "
                f"({ratio * 100:.0f}% от оригинала)."
            ),
            "source": "computed",
        }
    )
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

    hint = INTENSITY_HINTS.get(intensity, INTENSITY_HINTS["balanced"])
    style_hint = STYLE_PRESETS.get(style, "")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": SYSTEM_PROMPT,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": f"{hint}{style_hint}\n\n---\nCHAPTER TEXT:\n---\n{chapter_text}",
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
                raise ChapterEditError(
                    "The model returned the chapter with no changes at all. "
                    "Try again, or switch to a stronger intensity (e.g. "
                    "\"Thorough\") or a different style."
                )

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
