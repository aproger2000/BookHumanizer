"""Chapter Editor - минимальная версия для Render"""
import io
import json
import os
import re
import random
import time
import traceback
import logging
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "2.3.7"

ANTHROPIC_API_URL = os.environ.get(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_CHARS = 10_000
MAX_OUTPUT_TOKENS = 8_000

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


class ChapterEditError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are a professional editor. Your task is to rewrite the provided text to make it sound MORE HUMAN and LESS LIKE AI.

IMPORTANT RULES:
1. CHANGE EVERY SENTENCE. Do not leave any sentence unchanged.
2. Break long sentences into shorter ones.
3. Vary sentence structure - don't always start with subject.
4. Use filler words naturally: "well," "so," "you know," "actually."
5. Use dashes, ellipses, and breaks.
6. Keep the plot, characters, and facts EXACTLY the same.

The rewritten text should be approximately the same length as the original.

Respond with JSON: {"revised_text": "the rewritten text", "summary": "brief summary", "changes": ["change1", "change2"]}"""

STYLE_PRESETS = {
    "neutral": "",
    "dynamic_scifi": (
        "\n\nWrite in a brisk, cinematic style, like a sci-fi thriller."
    ),
}


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/health")
def health():
    return jsonify(status="ok", version=APP_VERSION)


@app.post("/api/revise")
def api_revise():
    logger.info("=== api_revise: START ===")
    
    try:
        file_storage = request.files.get("file")
        text = request.form.get("text", "")
        style = request.form.get("style", "neutral")

        # Получаем текст
        if file_storage and file_storage.filename:
            raw = file_storage.read()
            try:
                chapter_text = raw.decode("utf-8", errors="replace")
            except Exception:
                return jsonify(detail="Could not read file"), 400
        elif text.strip():
            chapter_text = text
        else:
            return jsonify(detail="Provide chapter text or upload a file."), 400

        chapter_text = chapter_text.strip()
        if not chapter_text:
            return jsonify(detail="Chapter text is empty."), 400
        
        # Ограничиваем длину
        if len(chapter_text) > MAX_CHARS:
            chapter_text = chapter_text[:MAX_CHARS]
            logger.warning(f"Truncated text to {MAX_CHARS} chars")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return jsonify(detail="ANTHROPIC_API_KEY not set"), 502

        style_hint = STYLE_PRESETS.get(style, "")
        
        user_content = f"{style_hint}\n\nText to rewrite:\n\n{chapter_text}"
        
        payload = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SYSTEM_PROMPT,
            "stream": True,
            "messages": [{"role": "user", "content": user_content}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        logger.info("Sending request to Anthropic...")
        
        try:
            anthropic_resp = requests.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(10, 120)
            )
        except requests.exceptions.Timeout:
            return jsonify(detail="Request timed out"), 504
        except requests.exceptions.RequestException as e:
            return jsonify(detail=f"Network error: {str(e)}"), 502

        if anthropic_resp.status_code != 200:
            detail = anthropic_resp.text[:500]
            anthropic_resp.close()
            return jsonify(detail=f"API error: {detail}"), 502

        logger.info("Connected to Anthropic, streaming response...")
        estimated_total = len(chapter_text)

        def generate():
            full_text = ""
            last_ping = time.time()
            
            try:
                for line in anthropic_resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    
                    payload_data = line[len("data:") :].strip()
                    if not payload_data:
                        continue
                    
                    try:
                        event = json.loads(payload_data)
                    except json.JSONDecodeError:
                        continue
                    
                    event_type = event.get("type")
                    
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_chunk = delta.get("text", "")
                            full_text += text_chunk
                            
                            pct = min(95, int(len(full_text) / max(estimated_total, 1) * 100))
                            yield _sse("progress", {
                                "chars": len(full_text),
                                "estimated_total": estimated_total,
                                "percent": pct
                            })
                            
                            if time.time() - last_ping > 5:
                                yield _sse("ping", {"percent": pct})
                                last_ping = time.time()
                    
                    elif event_type == "message_stop":
                        break
                    
                    elif event_type == "error":
                        error_msg = event.get("error", {}).get("message", "unknown error")
                        yield _sse("error", {"detail": error_msg})
                        return
                
                logger.info(f"Received {len(full_text)} chars from Anthropic")
                
                if not full_text.strip():
                    yield _sse("error", {
                        "detail": "The model returned an empty response. Please try again with a shorter text.",
                        "type": "EmptyResponse"
                    })
                    return
                
                try:
                    start = full_text.find("{")
                    end = full_text.rfind("}") + 1
                    
                    if start == -1 or end == 0:
                        yield _sse("error", {
                            "detail": "Could not parse response from AI. Please try again with shorter text.",
                            "type": "JSONError"
                        })
                        return
                    
                    json_str = full_text[start:end]
                    data = json.loads(json_str)
                    
                    revised_text = data.get("revised_text", "")
                    
                    if not revised_text:
                        yield _sse("error", {
                            "detail": "Revised text is empty. Please try again.",
                            "type": "EmptyText"
                        })
                        return
                    
                    revised_text = revised_text.replace("—", "-")
                    
                    yield _sse("done", {
                        "revised_text": revised_text,
                        "original_text": chapter_text,
                        "summary": data.get("summary", "Edited"),
                        "changes": data.get("changes", ["Text rewritten for natural flow"]),
                        "checklist": []
                    })
                    
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parse error: {e}")
                    logger.error(f"Response start: {full_text[:200]}")
                    yield _sse("error", {
                        "detail": "Could not parse response from AI. Please try again with shorter text.",
                        "type": "JSONError"
                    })
                except ChapterEditError as e:
                    yield _sse("error", {"detail": str(e), "type": "EditError"})
                    
            except requests.exceptions.Timeout:
                yield _sse("error", {"detail": "Connection timed out"})
            except Exception as e:
                logger.exception("Error in generate")
                yield _sse("error", {"detail": f"Error: {str(e)}"})
            finally:
                anthropic_resp.close()
                logger.info("=== generate: FINISHED ===")

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no"
            }
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
