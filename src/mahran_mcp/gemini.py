#!/usr/bin/env python3
"""
gemini_mcp — a Model Context Protocol server exposing Google Gemini as tools,
talking to the Gemini REST API directly via httpx (no google-genai SDK, so no
native build dependencies).

Tools:
  - gemini_ask            : text question / second opinion (optional Google Search grounding)
  - gemini_analyze_image  : analyze a local image file
  - gemini_analyze_video  : analyze a local video file (inline, size-limited)
  - gemini_analyze_url     : analyze a web page / public image / YouTube URL
  - gemini_generate_image : generate an image from a prompt, save to disk
  - gemini_long_context   : reason over one or more large local files
  - gemini_count_tokens   : count tokens for a text
  - gemini_list_models    : list available Gemini models

The API key is read from GEMINI_API_KEY or ~/.gemini_mcp/config.json.
Use the configuration CLI to save a key. Never hardcode it.

Part of the Mahran MCP toolkit.
"""

import os
import asyncio
import random
import json
import base64
import mimetypes
from pathlib import Path
from typing import Optional, List

import httpx
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".gemini_mcp"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_api_key() -> str:
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key.strip()

    if CONFIG_FILE.exists():
        try:
            saved = (json.loads(CONFIG_FILE.read_text()).get("api_key") or "").strip()
            if saved:
                return saved
        except Exception:
            pass

    return ""



def _api_key() -> str:
    key = _load_api_key()
    if not key:
        raise ValueError("Set GEMINI_API_KEY in the MCP client environment, or save api_key in ~/.gemini_mcp/config.json. Never commit this file.")
    return key

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

INLINE_LIMIT_BYTES = 18 * 1024 * 1024  # ~18 MB inline request ceiling
TIMEOUT = 40.0

mcp = FastMCP("gemini_mcp")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_file(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}. Pass an absolute path.")
    if not p.is_file():
        raise ValueError(f"Path is not a file: {p}")
    return p


def _inline_part(p: Path) -> dict:
    data = p.read_bytes()
    if len(data) > INLINE_LIMIT_BYTES:
        raise ValueError(
            f"File too large for inline upload ({len(data) // (1024*1024)} MB; "
            f"limit ~18 MB). Use a smaller/compressed file."
        )
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


async def _post(model: str, action: str, payload: dict) -> dict:
    # Header authentication keeps keys out of URL logs and exception strings.
    headers = {"x-goog-api-key": _api_key()}
    url = f"{BASE_URL}/models/{model}:{action}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt == 2:
                    raise
            await asyncio.sleep(2 ** attempt + random.uniform(0, 0.25))
    raise RuntimeError("Gemini retries exhausted")


def _extract_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        texts = [p["text"] for p in parts if "text" in p]
        return "\n".join(texts) if texts else "(Gemini returned no text.)"
    except (KeyError, IndexError):
        fb = data.get("promptFeedback", {})
        if fb:
            return f"(No answer — blocked or empty. promptFeedback: {json.dumps(fb)})"
        return "(Gemini returned an unexpected response.)"


def _handle_error(e: Exception) -> str:
    if isinstance(e, (FileNotFoundError, ValueError)):
        return f"Error: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        body = ""
        try:
            body = e.response.json().get("error", {}).get("message", "")
        except Exception:
            body = e.response.text[:200]
        if code in (400, 401, 403):
            return (f"Error: Gemini rejected the request ({code}). Often a bad/disabled API "
                    f"key or unsupported model. Detail: {body}")
        if code == 404:
            return f"Error: Model not found (404). Check the name with gemini_list_models. {body}"
        if code == 429:
            return "Error: Rate limit or quota exceeded (429). Wait, or check billing."
        return f"Error: Gemini API returned {code}. {body}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Try again or use a smaller input."
    return f"Error: {type(e).__name__}: {e}"


async def _generate(model: str, parts: list, tools=None, thinking_budget=None) -> str:
    payload: dict = {"contents": [{"parts": parts}]}
    if tools:
        payload["tools"] = tools
    if thinking_budget is not None:
        payload["generationConfig"] = {"thinkingConfig": {"thinkingBudget": thinking_budget}}

    data = await _post(model, "generateContent", payload)
    return _extract_text(data)



# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class AskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt: str = Field(..., description="The question or instruction for Gemini.",
                        min_length=1, max_length=100000)
    use_search: bool = Field(default=False,
                             description="If true, let Gemini use Google Search grounding.")
    thinking_budget: Optional[int] = Field(
        default=None, ge=0, le=24576,
        description="Optional reasoning budget in tokens (0 disables extended thinking).")
    model: str = Field(default="", description="Optional model override, e.g. 'gemini-2.5-pro'.")

    @field_validator("prompt")
    @classmethod
    def _p(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt cannot be empty.")
        return v.strip()


class ImageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    image_path: str = Field(..., description="Absolute path to a local image.", min_length=1)
    prompt: str = Field(default="Describe this image in detail.",
                        description="What Gemini should do with the image.")
    model: str = Field(default="", description="Optional model override.")


class VideoInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    video_path: str = Field(..., description="Absolute path to a local video (<~18 MB).",
                            min_length=1)
    prompt: str = Field(default="Describe what happens in this video.",
                        description="What Gemini should do with the video.")
    model: str = Field(default="", description="Optional model override.")


class UrlInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(..., description="Public URL: web page, public image, or YouTube link.",
                     min_length=4)
    prompt: str = Field(default="Summarize this and extract the key points.",
                        description="What Gemini should do with the content.")
    model: str = Field(default="", description="Optional model override.")

    @field_validator("url")
    @classmethod
    def _u(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class GenImageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt: str = Field(..., description="Text description of the image to generate.",
                        min_length=1, max_length=5000)
    output_path: str = Field(..., description="Absolute path to save the PNG.", min_length=1)
    model: str = Field(default="", description="Optional image model override.")


class LongContextInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    file_paths: List[str] = Field(..., description="Absolute paths to local files.",
                                  min_length=1, max_length=20)
    prompt: str = Field(..., description="The instruction across all files.", min_length=1)
    model: str = Field(default="", description="Optional model override.")


class CountTokensInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(..., description="Text to count tokens for.", min_length=1)
    model: str = Field(default="", description="Optional model override.")


class CritiqueInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    content: str = Field(..., description="The idea, plan, code, or text to critique.",
                        min_length=1, max_length=100000)
    focus: str = Field(default="",
                       description="Optional angle to focus the critique on "
                                   "(e.g. 'security', 'feasibility', 'novelty').")
    use_search: bool = Field(default=True,
                             description="Ground the critique in up-to-date web info (default on).")
    model: str = Field(default="", description="Optional model override.")


class ListModelsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: str = Field(default="", description="Optional substring to filter model names.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="gemini_ask",
          annotations={"title": "Ask Gemini", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_ask(params: AskInput) -> str:
    """Ask Gemini a text question. Set use_search=true for live Google Search grounding."""
    try:
        tools = [{"google_search": {}}] if params.use_search else None
        return await _generate(params.model or DEFAULT_MODEL, [{"text": params.prompt}],
                               tools=tools, thinking_budget=params.thinking_budget)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_analyze_image",
          annotations={"title": "Analyze Image", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_analyze_image(params: ImageInput) -> str:
    """Send a local image file to Gemini and return its analysis."""
    try:
        p = _resolve_file(params.image_path)
        return await _generate(params.model or DEFAULT_MODEL,
                               [_inline_part(p), {"text": params.prompt}])
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_analyze_video",
          annotations={"title": "Analyze Video", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_analyze_video(params: VideoInput) -> str:
    """Send a local video file (inline, ~18 MB max) to Gemini and return its analysis."""
    try:
        p = _resolve_file(params.video_path)
        return await _generate(params.model or DEFAULT_MODEL,
                               [_inline_part(p), {"text": params.prompt}])
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_analyze_url",
          annotations={"title": "Analyze URL", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_analyze_url(params: UrlInput) -> str:
    """Analyze a public web page, image, or YouTube URL without downloading it first."""
    try:
        low = params.url.lower()
        if "youtube.com" in low or "youtu.be" in low:
            parts = [{"fileData": {"fileUri": params.url}}, {"text": params.prompt}]
            return await _generate(params.model or DEFAULT_MODEL, parts)
        parts = [{"text": f"{params.prompt}\n\nURL: {params.url}"}]
        return await _generate(params.model or DEFAULT_MODEL, parts,
                               tools=[{"url_context": {}}])
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_generate_image",
          annotations={"title": "Generate Image", "readOnlyHint": False, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_generate_image(params: GenImageInput) -> str:
    """Generate an image from a text prompt and save it as a local PNG."""
    try:
        out = Path(params.output_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        data = await _post(params.model or IMAGE_MODEL, "generateContent",
                           {"contents": [{"parts": [{"text": params.prompt}]}]})
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                out.write_bytes(base64.b64decode(inline["data"]))
                return json.dumps({"status": "ok", "saved_to": str(out)}, indent=2)
        return "Error: Gemini returned no image data (prompt may have been refused)."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_long_context",
          annotations={"title": "Reason over Files", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_long_context(params: LongContextInput) -> str:
    """Send one or more local files to Gemini for large-context reasoning."""
    try:
        parts = [_inline_part(_resolve_file(fp)) for fp in params.file_paths]
        parts.append({"text": params.prompt})
        return await _generate(params.model or DEFAULT_MODEL, parts)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_critique",
          annotations={"title": "Get a Critical Second Opinion from Gemini", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
async def gemini_critique(params: CritiqueInput) -> str:
    """Ask Gemini for a blunt, critical second opinion on an idea, plan, code, or text —
    no flattery. Returns strengths, weaknesses, risks, and a clear verdict.

    Returns:
        str: Gemini's critique, or an "Error: ..." string.
    """
    try:
        focus_line = f"\nركّز بشكل خاص على: {params.focus}." if params.focus else ""
        prompt = (
            "إنت خبير ناقد صريح. راجع التالي نقدياً من غير مجاملة. اذكر: "
            "(1) نقاط القوة الحقيقية، (2) نقاط الضعف والمخاطر، (3) أي حاجة مكرّرة أو "
            "موجودة بالفعل في السوق، (4) حكم نهائي واضح مع سبب. لو فيه مشكلة جوهرية قولها "
            f"بصراحة.{focus_line}\n\n--- المحتوى ---\n{params.content}"
        )
        tools = [{"google_search": {}}] if params.use_search else None
        return await _generate(params.model or DEFAULT_MODEL, [{"text": prompt}], tools=tools)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_count_tokens",
          annotations={"title": "Count Tokens", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_count_tokens(params: CountTokensInput) -> str:
    """Count how many tokens a text uses for a given model."""
    try:
        data = await _post(params.model or DEFAULT_MODEL, "countTokens",
                           {"contents": [{"parts": [{"text": params.text}]}]})
        return json.dumps({"total_tokens": data.get("totalTokens")}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="gemini_list_models",
          annotations={"title": "List Models", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": False, "openWorldHint": True})
async def gemini_list_models(params: ListModelsInput) -> str:
    """List available Gemini models, optionally filtered by a substring."""
    try:
        url = f"{BASE_URL}/models?pageSize=100"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers={"x-goog-api-key": _api_key()})
            resp.raise_for_status()
            data = resp.json()
        names = [m.get("name", "") for m in data.get("models", [])]
        if params.filter:
            names = [n for n in names if params.filter.lower() in n.lower()]
        return json.dumps({"count": len(names), "models": names}, indent=2)
    except Exception as e:
        return _handle_error(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
