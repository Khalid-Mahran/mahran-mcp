#!/usr/bin/env python3
"""
mahran_mcp — a Model Context Protocol server exposing a LOCAL Ollama model as tools.

Named "Mahran": a local AI model (when using the default loopback Ollama endpoint) that runs
alongside Claude and Gemini. Use it to analyze sensitive code or data that must
never leave the machine.

Talks to the Ollama REST API on http://localhost:11434 (no API key needed).

Tools:
  - mahran_ask           : ask the local model a text question
  - mahran_analyze_code  : security-focused review of a code snippet (stays local)
  - mahran_analyze_file  : read a local file and analyze it (stays local)
  - mahran_list_models   : list models installed in Ollama

Part of the Mahran MCP toolkit. No private persona is bundled.
"""

import os
import json
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# Default model — good at code. Override with MAHRAN_MODEL. On a slower Mac,
# a lighter model like "qwen2.5-coder:3b" or "llama3.2:3b" responds faster.
DEFAULT_MODEL = os.environ.get("MAHRAN_MODEL", "qwen2.5:7b")
TIMEOUT = 600.0  # local CPU inference can be slow; give it room

# --- Router: specialist models for each kind of task ---
GENERAL_MODEL = os.environ.get("MAHRAN_GENERAL_MODEL", DEFAULT_MODEL)
CODE_MODEL = os.environ.get("MAHRAN_CODE_MODEL", DEFAULT_MODEL)
CYBER_MODEL = os.environ.get("MAHRAN_CYBER_MODEL", DEFAULT_MODEL)

# Optional user-authored persona file; private Modelfiles are never distributed.
def _read_persona() -> str:
    filename = os.environ.get("MAHRAN_PERSONA_FILE", "")
    return Path(filename).expanduser().read_text() if filename else ""


PERSONA = _read_persona()

mcp = FastMCP("mahran_mcp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_file(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}. Pass an absolute path.")
    if not p.is_file():
        raise ValueError(f"Path is not a file: {p}")
    return p


def _handle_error(e: Exception) -> str:
    if isinstance(e, (FileNotFoundError, ValueError)):
        return f"Error: {e}"
    if isinstance(e, httpx.ConnectError):
        return ("Error: Can't reach Ollama at " + OLLAMA_URL + ". Make sure Ollama is "
                "installed and running (open the Ollama app, or run `ollama serve`).")
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        body = e.response.text[:200]
        if code == 404:
            return (f"Error: Model not found in Ollama. Pull it first, e.g. "
                    f"`ollama pull {DEFAULT_MODEL}`. Detail: {body}")
        return f"Error: Ollama returned {code}. {body}"
    if isinstance(e, httpx.TimeoutException):
        return ("Error: Local model timed out. Try a smaller model "
                "(set MAHRAN_MODEL=qwen2.5-coder:3b) or a shorter input.")
    return f"Error: {type(e).__name__}: {e}"


async def _generate(model: str, prompt: str, system: str = "") -> str:
    """Call Ollama /api/generate (non-streaming) and return the text.

    `system` optionally overrides/sets the system prompt for this call (used by
    the router to apply the shared Mahran persona to any specialist model).
    """
    url = f"{OLLAMA_URL}/api/generate"
    payload = {"model": model or DEFAULT_MODEL, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or "(empty response)"


# ---------------------------------------------------------------------------
# Router — pick the best specialist model for a task
# ---------------------------------------------------------------------------

_CODE_HINTS = ("code", "function", "bug", "python", "java", "php", "sql", "api",
               "script", "compile", "regex", "كود", "دالة", "برمج", "سكريبت",
               "باگ", "خطأ برمجي")
_CYBER_HINTS = ("exploit", "payload", "vulnerab", "cve", "xss", "sqli", "pentest",
                "injection", "privilege", "reverse shell", "malware", "recon",
                "ثغرة", "ثغرات", "اختراق", "اختبار اختراق", "حقن", "استغلال",
                "هجوم", "تصعيد")


def _classify(prompt: str) -> str:
    """Return 'cyber', 'code', or 'general' for a prompt (keyword-based)."""
    low = prompt.lower()
    cyber = sum(1 for h in _CYBER_HINTS if h in low)
    code = sum(1 for h in _CODE_HINTS if h in low)
    if cyber and cyber >= code:
        return "cyber"
    if code:
        return "code"
    return "general"


_ROUTE_MODELS = {"general": GENERAL_MODEL, "code": CODE_MODEL, "cyber": CYBER_MODEL}


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class AskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt: str = Field(..., description="The question or instruction for the local model.",
                        min_length=1, max_length=100000)
    model: str = Field(default="", description="Optional Ollama model override.")

    @field_validator("prompt")
    @classmethod
    def _p(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt cannot be empty.")
        return v.strip()


class CodeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(..., description="The code snippet to review for security issues.",
                      min_length=1, max_length=100000)
    language: str = Field(default="", description="Optional language hint (php, java, python...).")
    model: str = Field(default="", description="Optional Ollama model override.")


class FileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    file_path: str = Field(..., description="Absolute path to a local text/code file.",
                           min_length=1)
    prompt: str = Field(default="Analyze this file and summarize what it does and any concerns.",
                        description="What the local model should do with the file.")
    model: str = Field(default="", description="Optional Ollama model override.")


class ListModelsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: str = Field(default="", description="Optional substring to filter model names.")


class RouteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt: str = Field(..., description="The question/task. The router picks the best "
                                         "local model for it automatically.",
                        min_length=1, max_length=100000)
    force: str = Field(default="",
                       description="Optional: force a lane — 'general', 'code', or 'cyber'. "
                                   "Leave empty to auto-route.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="mahran_ask",
          annotations={"title": "Ask Mahran (local)", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
async def mahran_ask(params: AskInput) -> str:
    """Ask the local Ollama model a text question. Uses your configured Ollama endpoint (localhost by default)."""
    try:
        return await _generate(params.model or DEFAULT_MODEL, params.prompt)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="mahran_analyze_code",
          annotations={"title": "Local Secure Code Review", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def mahran_analyze_code(params: CodeInput) -> str:
    """Review a code snippet for security vulnerabilities using the local model —
    for sensitive code that must not leave the machine."""
    try:
        lang = f" (language: {params.language})" if params.language else ""
        prompt = (
            f"You are a security code reviewer. Review this code{lang} for vulnerabilities "
            "(injection, auth, secrets, unsafe calls). List each issue, its severity, and a "
            f"concrete fix. Be specific.\n\n```\n{params.code}\n```"
        )
        return await _generate(params.model or DEFAULT_MODEL, prompt)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="mahran_analyze_file",
          annotations={"title": "Analyze Local File", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def mahran_analyze_file(params: FileInput) -> str:
    """Read a local text/code file and analyze it with the local model. Uses your configured Ollama endpoint."""
    try:
        p = _resolve_file(params.file_path)
        try:
            text = p.read_text(errors="replace")
        except Exception:
            return "Error: Could not read the file as text (binary file?)."
        if len(text) > 100000:
            text = text[:100000] + "\n...[truncated]"
        prompt = f"{params.prompt}\n\n--- FILE: {p.name} ---\n{text}"
        return await _generate(params.model or DEFAULT_MODEL, prompt)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="mahran_route",
          annotations={"title": "Mahran Router (auto-pick local model)", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
async def mahran_route(params: RouteInput) -> str:
    """Route a question to the best LOCAL specialist model and answer with the
    shared Mahran persona applied:
      - general, code, cyber -> configurable Ollama model names

    Use `force` to override the automatic choice. Uses the configured Ollama endpoint.

    Returns:
        str: The chosen lane + model, then the answer. Or an "Error: ..." string.
    """
    try:
        lane = params.force.strip().lower() or _classify(params.prompt)
        if lane not in _ROUTE_MODELS:
            lane = "general"
        model = _ROUTE_MODELS[lane]
        # Apply the shared persona to specialist models (the 'mahran' model
        # already has it baked in, but re-applying is harmless).
        answer = await _generate(model, params.prompt, system=PERSONA)
        header = f"[lane: {lane} | model: {model}]\n"
        return header + answer
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="mahran_list_models",
          annotations={"title": "List Local Models", "readOnlyHint": True,
                       "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def mahran_list_models(params: ListModelsInput) -> str:
    """List the models installed locally in Ollama (via /api/tags)."""
    try:
        url = f"{OLLAMA_URL}/api/tags"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        names = [m.get("name", "") for m in data.get("models", [])]
        if params.filter:
            names = [n for n in names if params.filter.lower() in n.lower()]
        if not names:
            return ("No models installed in Ollama yet. Pull one, e.g. "
                    f"`ollama pull {DEFAULT_MODEL}`.")
        return json.dumps({"count": len(names), "models": names}, indent=2)
    except Exception as e:
        return _handle_error(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
