"""Explicitly opt-in shell access with bounded output and execution time."""
import asyncio
import os
import signal
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

mcp = FastMCP("mahran_terminal")
LIMIT = 32_768

class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=20_000, description="Shell command to execute with the current user's permissions.")
    cwd: str = Field(default="", description="Existing working directory; defaults to the server's directory.")
    timeout_seconds: int = Field(default=30, ge=1, le=120)

async def _capture(stream):
    data = bytearray()
    truncated = False
    while chunk := await stream.read(8192):
        available = max(0, LIMIT - len(data))
        data.extend(chunk[:available])
        truncated |= len(chunk) > available
    return data.decode(errors="replace") + ("\n[output truncated]" if truncated else "")

@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
async def terminal_run_command(params: CommandInput) -> dict:
    """Execute a shell command. May modify/delete files or access the network; review each invocation."""
    cwd = Path(params.cwd).expanduser() if params.cwd else Path.cwd()
    if not cwd.is_dir():
        return {"error": "Working directory does not exist."}
    process = await asyncio.create_subprocess_shell(
        params.command, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=(os.name != "nt"))
    out = asyncio.create_task(_capture(process.stdout))
    err = asyncio.create_task(_capture(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), params.timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.gather(out, err), 2)
    except asyncio.TimeoutError:
        stdout, stderr = "", "Output pipe did not close; a child process may still be running."
    return {"exit_code": process.returncode, "timed_out": timed_out, "stdout": stdout, "stderr": stderr}

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()
