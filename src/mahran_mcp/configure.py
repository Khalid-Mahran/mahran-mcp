"""Generate client configuration without changing existing client settings."""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FILESYSTEM_VERSION = "2.1.0"  # Tested release; change deliberately and re-test.

def make_servers(python: str, model: str, terminal: bool = False) -> dict:
    servers = {
        "gemini": {"command": python, "args": ["-m", "mahran_mcp.gemini"], "env": {"GEMINI_MODEL": "gemini-3.1-flash-lite"}},
        "mahran": {"command": python, "args": ["-m", "mahran_mcp.local"], "env": {"MAHRAN_MODEL": model}},
    }
    if terminal:
        servers["terminal"] = {"command": python, "args": ["-m", "mahran_mcp.terminal"]}
    return servers

def codex_config(servers: dict) -> str:
    lines = ["# Generated configuration fragment. Merge these tables into ~/.codex/config.toml.", ""]
    for name, config in servers.items():
        lines.append("[mcp_servers." + json.dumps(name) + "]")
        for key in ("command", "args"):
            lines.append(key + " = " + json.dumps(config[key], ensure_ascii=False))
        lines.append("startup_timeout_sec = 30")
        lines.append("tool_timeout_sec = " + ("650" if name == "mahran" else "150"))
        if name == "gemini":
            lines.append('env_vars = ["GEMINI_API_KEY"]')
        if config.get("env"):
            lines.append("[mcp_servers." + json.dumps(name) + ".env]")
            for key, value in config["env"].items():
                lines.append(key + " = " + json.dumps(value, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines)

def save_key() -> None:
    key = getpass.getpass("Your Gemini API key (hidden): ").strip()
    if not key:
        raise ValueError("No key entered; nothing saved.")
    directory = Path.home() / ".gemini_mcp"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "config.json"
    # Refuse symlinks; credentials must stay in the intended private file.
    if path.is_symlink():
        raise ValueError("Refusing to write a key through a symbolic link.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"api_key": key}, f)
    path.chmod(0o600)
    print("Key saved privately outside the repository. It is not included in generated configuration.")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude", "both"], default="both")
    parser.add_argument("--output-dir", type=Path, default=Path("generated"))
    parser.add_argument("--model", default="qwen2.5:7b", help="An Ollama model you have pulled.")
    parser.add_argument("--with-terminal", action="store_true", help="Opt in to arbitrary shell access.")
    parser.add_argument("--filesystem-dir", action="append", default=[], help="Allow a directory; repeat for multiple directories.")
    parser.add_argument("--install-filesystem", action="store_true", help="Download the official Node filesystem server into the output directory.")
    parser.add_argument("--save-gemini-key", action="store_true", help="Prompt for and save your own Gemini key, then exit.")
    options = parser.parse_args()
    try:
        if options.save_gemini_key:
            save_key()
            return
        directories = [str(Path(p).expanduser().resolve(strict=True)) for p in options.filesystem_dir]
        if any(not Path(p).is_dir() for p in directories):
            raise ValueError("Every filesystem path must be an existing directory.")
        if options.install_filesystem and not directories:
            raise ValueError("Provide at least one --filesystem-dir with --install-filesystem.")
        output = options.output_dir.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        servers = make_servers(os.path.abspath(sys.executable), options.model, options.with_terminal)
        if directories:
            node, npm = shutil.which("node"), shutil.which("npm")
            if not node or not npm:
                raise ValueError("Filesystem support needs Node.js 24+ and npm on PATH.")
            node_version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
            if int(node_version.lstrip("v").split(".")[0]) < 24:
                raise ValueError("Filesystem support needs Node.js 24 or newer; found " + node_version)
            runtime = output / ".runtime"
            script = runtime / "node_modules/@modelcontextprotocol/server-filesystem/dist/index.js"
            if options.install_filesystem:
                subprocess.run([npm, "install", "--prefix", str(runtime), "--cache", str(runtime / "npm-cache"),
                                "@modelcontextprotocol/server-filesystem@" + FILESYSTEM_VERSION,
                                "--no-audit", "--no-fund"], check=True, timeout=180)
            if not script.is_file():
                raise ValueError("Filesystem server is missing; add --install-filesystem.")
            servers["filesystem"] = {"command": node, "args": [str(script), *directories]}
        if options.client in ("both", "codex"):
            (output / "codex-mcp.toml").write_text(codex_config(servers), encoding="utf-8")
        if options.client in ("both", "claude"):
            (output / "claude-mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")
        print("Configuration written to", output)
        print("Merge the generated entries into your client configuration; existing settings were not modified.")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, "Configuration error: " + str(error) + "\n")

if __name__ == "__main__":
    main()
