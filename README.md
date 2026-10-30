# Mahran MCP

Portable MCP connections for **Gemini**, **local Ollama models**, **terminal commands**, and the **official filesystem server**. Works with Codex and Claude Desktop through stdio.

[الدليل بالعربي](README.ar.md)

This repository contains source code and a configuration generator. It does not include API keys, private model personas, model weights, or anyone's personal client settings. Each user supplies their own Gemini key and chooses their own folders and Ollama models.

## Requirements

- Python 3.11 or newer (tested with Python 3.12).
- A personal [Gemini API key](https://aistudio.google.com/apikey) for Gemini tools. API usage may incur charges from Google.
- [Ollama](https://ollama.com/) running locally for local tools. Model downloads are separate and have their own licenses.
- Node.js 24+ and npm only if filesystem access is wanted.

The package intentionally uses the tested MCP Python SDK 1.x API, pinned in `pyproject.toml`; SDK 2.x migration is separate work.

## Install

Clone this repository and install it:

```sh
git clone https://github.com/Khalid-Mahran/mahran-mcp.git
cd mahran-mcp
python3 -m venv .venv
.venv/bin/python -m pip install --prefer-binary .
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe` in commands below. Local checks were run on macOS. CI is configured for Linux and macOS; Windows support has not been tested here.

Save **your own** Gemini key with the hidden prompt:

```sh
.venv/bin/python -m mahran_mcp.configure --save-gemini-key
```

This stores it in `~/.gemini_mcp/config.json`, outside the repository, with restrictive file permissions on Unix. Alternatively, set `GEMINI_API_KEY` in your MCP client's environment. Environment variables take priority. A `.env` file is not loaded automatically.

Prepare a local model:

```sh
ollama pull qwen2.5:7b
```

Make sure Ollama is running (`ollama serve`, or open its app). You may choose another installed model with `--model` below. No custom `mahran`, `mahran-coder`, or `mahran-cyber` models are required.

## Generate your client settings

Basic Gemini + local Ollama connections:

```sh
.venv/bin/python -m mahran_mcp.configure --client both
```

To include terminal commands and access to a folder you choose:

```sh
.venv/bin/python -m mahran_mcp.configure --client both \
  --with-terminal --install-filesystem --filesystem-dir "/absolute/path/to/your/project"
```

Replace that example directory with an existing folder on your machine. Repeat `--filesystem-dir` to allow additional folders. The official filesystem package is downloaded into `generated/.runtime` using a separate npm cache, then launched directly with Node. The pinned filesystem release requires Node.js 24 or newer. This avoids permissions problems in an existing global npm cache and avoids downloads during MCP startup. Keep `generated/.runtime` in place after configuring your client.

The generator writes:

- `generated/codex-mcp.toml`: merge its tables into `~/.codex/config.toml`.
- `generated/claude-mcp.json`: merge its `mcpServers` entries into Claude Desktop's configuration.

Claude Desktop config locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Preserve any existing client configuration, and replace entries with the same names instead of duplicating them. Restart your client after merging. Generated paths are specific to **your** installation; regenerate them when moving the environment, and do not upload the `generated` directory.

## Tools

| Server | Tools |
| --- | --- |
| Gemini | Ask, critique, analyze images/video/URLs, reason over files, generate images, count tokens, list models |
| Ollama | Ask, review code, analyze a text file, list models, route to a specialist |
| Terminal (opt-in) | `terminal_run_command`: shell execution with a timeout and bounded output |
| Filesystem (opt-in) | Official Node MCP filesystem tools restricted to explicitly selected roots |

Gemini tools send their prompt and selected content to Google's API. `gemini_long_context` uploads the selected files. The local server sends data to `OLLAMA_URL` (loopback by default); setting a remote URL sends that data off the machine. It does not enforce the filesystem server's selected roots. Terminal commands run with your user account's permissions and can modify/delete files or access the network. Enable them only for a trusted client and review its tool approvals. Tool annotations are hints, not a permission boundary.

## Options

| Environment variable | Default / meaning |
| --- | --- |
| `GEMINI_API_KEY` | User-supplied key; otherwise `~/.gemini_mcp/config.json` |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite`; change to an available model from `gemini_list_models` |
| `GEMINI_IMAGE_MODEL` | `gemini-3.1-flash-image`; requires an account/model that supports image generation |
| `OLLAMA_URL` | `http://localhost:11434` |
| `MAHRAN_MODEL` | `qwen2.5:7b` |
| `MAHRAN_GENERAL_MODEL` | Same as `MAHRAN_MODEL` |
| `MAHRAN_CODE_MODEL` | Same as `MAHRAN_MODEL` |
| `MAHRAN_CYBER_MODEL` | Same as `MAHRAN_MODEL` |
| `MAHRAN_PERSONA_FILE` | Optional path to your own plain-text system prompt |

The generated Gemini config sets `GEMINI_MODEL` explicitly; edit that entry to choose another model. The three router lanes are keyword-based and use the same model until you configure specialist overrides. Gemini requests retry transient failures up to three attempts with short backoff; unsupported models and invalid credentials fail without silent model substitution. Availability, quotas, and supported features depend on your account. Large local models may require more RAM and time.

## Troubleshooting

- **Missing Gemini key:** use `--save-gemini-key`, then restart the client.
- **404 model unavailable:** call `gemini_list_models` and select an available model; old names may still be listed even when generation is unavailable to your account.
- **503 / timeout:** requests retry briefly; persistent service outages must recover upstream, or select another available model.
- **Ollama unavailable:** start Ollama and verify `ollama list`.
- **Filesystem missing:** regenerate with `--install-filesystem` and an existing `--filesystem-dir`.
- **Client cannot start Python:** regenerate using the installed virtual environment's Python, then merge the new paths.
- **Long local inference:** the generated Codex tool timeout is 650 seconds. Adjust other clients' timeouts if needed.

## Development and checks

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src
```

Tests use mocked API responses, so contributors need no keys and incur no API charges. They cover transient retries, authentication headers, missing-key behavior, generated config, terminal timeout/output handling, and MCP startup/tool discovery. CI runs these checks on Linux and macOS. Text generation and filesystem initialization were also checked locally; image/video/search features were not all exercised against paid services.

## Publish your fork

Create a GitHub repository and upload the contents of this folder, including hidden files such as `.gitignore` and `.github`. Exclude `.venv`, `generated`, keys, `.env`, backups, and personal model files. Do not upload an existing personal Codex or Claude configuration. No repository has been created by this package.

## License and upstream software

This toolkit's source is available under the [MIT License](LICENSE). The filesystem server is installed separately from [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem); its license and dependencies remain upstream. MCP Python SDK, HTTPX, Pydantic, Ollama model weights, and Google services retain their own licenses and terms.
