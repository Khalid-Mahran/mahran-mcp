import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mahran_mcp import gemini, local, terminal
from mahran_mcp.configure import make_servers, codex_config

class ConfigTests(unittest.TestCase):
    def test_codex_round_trip_with_quoted_paths(self):
        servers = make_servers('C:\\A Folder\\python.exe', 'qwen2.5:7b', True)
        parsed = tomllib.loads(codex_config(servers))['mcp_servers']
        self.assertEqual(parsed['gemini']['command'], servers['gemini']['command'])
        self.assertEqual(parsed['gemini']['env_vars'], ['GEMINI_API_KEY'])
        self.assertEqual(parsed['mahran']['tool_timeout_sec'], 650)
        self.assertIn('terminal', parsed)

    def test_terminal_is_opt_in(self):
        self.assertNotIn('terminal', make_servers(sys.executable, 'test'))

    def test_cli_creates_both_configs_without_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, '-m', 'mahran_mcp.configure', '--output-dir', tmp], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            claude = json.loads((Path(tmp)/'claude-mcp.json').read_text())
            self.assertNotIn('GEMINI_API_KEY', claude['mcpServers']['gemini']['env'])
            self.assertEqual(set(claude['mcpServers']), {'gemini', 'mahran'})
            tomllib.loads((Path(tmp)/'codex-mcp.toml').read_text())

    def test_cli_rejects_missing_filesystem_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, '-m', 'mahran_mcp.configure', '--filesystem-dir', tmp+'/missing'], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)

class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_is_actionable_without_network(self):
        with patch.object(gemini, '_load_api_key', return_value=''):
            answer = await gemini.gemini_ask(gemini.AskInput(prompt='test'))
            self.assertIn('Set GEMINI_API_KEY', answer)

    async def test_retries_503_with_header_auth(self):
        statuses = iter([503, 200])
        seen = []
        async def handler(request):
            seen.append(request)
            status = next(statuses)
            return httpx.Response(status, json={'candidates':[{'content':{'parts':[{'text':'OK'}]}}]} if status == 200 else {'error':{'message':'busy'}})
        real_client = httpx.AsyncClient
        with patch.object(gemini, '_api_key', return_value='fake-test-key'), patch.object(gemini.httpx, 'AsyncClient', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)), patch.object(gemini.asyncio, 'sleep', new=AsyncMock()):
            data = await gemini._post('test-model', 'generateContent', {})
        self.assertEqual(gemini._extract_text(data), 'OK')
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].headers['x-goog-api-key'], 'fake-test-key')
        self.assertNotIn('key=', str(seen[0].url))

    async def test_403_does_not_retry(self):
        hits = []
        async def handler(request):
            hits.append(1)
            return httpx.Response(403, json={'error': {'message':'denied'}})
        real_client = httpx.AsyncClient
        with patch.object(gemini, '_api_key', return_value='fake-test-key'), patch.object(gemini.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            with self.assertRaises(httpx.HTTPStatusError):
                await gemini._post('test', 'generateContent', {})
        self.assertEqual(len(hits), 1)

    async def test_timeout_attempts_are_bounded(self):
        hits = []
        async def handler(request):
            hits.append(1)
            raise httpx.ReadTimeout('timeout', request=request)
        real_client = httpx.AsyncClient
        with patch.object(gemini, '_api_key', return_value='fake-test-key'), patch.object(gemini.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)), patch.object(gemini.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(httpx.TimeoutException):
                await gemini._post('test', 'generateContent', {})
        self.assertEqual(len(hits), 3)

class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_uses_configured_model_and_persona(self):
        with patch.object(local, '_generate', new=AsyncMock(return_value='answer')) as generate:
            result = await local.mahran_route(local.RouteInput(prompt='Review this python function'))
            self.assertIn('lane: code', result)
            generate.assert_awaited_once_with(local.CODE_MODEL, 'Review this python function', system=local.PERSONA)

class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_command(self):
        answer = await terminal.terminal_run_command(terminal.CommandInput(command='echo MCP_OK'))
        self.assertEqual(answer['exit_code'], 0)
        self.assertIn('MCP_OK', answer['stdout'])

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group timeout test')
    async def test_timeout(self):
        answer = await terminal.terminal_run_command(terminal.CommandInput(command='sleep 5', timeout_seconds=1))
        self.assertTrue(answer['timed_out'])

    async def test_output_is_bounded(self):
        class Stream:
            def __init__(self): self.chunks = iter([b'x'*8192]*20 + [b''])
            async def read(self, n): return next(self.chunks)
        output = await terminal._capture(Stream())
        self.assertIn('[output truncated]', output)
        self.assertLess(len(output), terminal.LIMIT + 100)

class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_servers_start_and_discover_without_api_keys(self):
        for module, expected in [('gemini','gemini_ask'),('local','mahran_ask'),('terminal','terminal_run_command')]:
            env = dict(os.environ, GEMINI_API_KEY='')
            with tempfile.TemporaryDirectory() as tmp:
                # Missing private key files must not prevent startup/discovery.
                env.update(HOME=tmp, USERPROFILE=tmp)
                async with asyncio.timeout(20):
                    async with stdio_client(StdioServerParameters(command=sys.executable, args=['-m','mahran_mcp.'+module], env=env)) as (read,write):
                        async with ClientSession(read,write) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            self.assertIn(expected,[t.name for t in tools.tools])
                            if module == 'gemini':
                                response = await session.call_tool('gemini_ask', {'params':{'prompt':'test'}})
                                self.assertIn('Set GEMINI_API_KEY', response.content[0].text)

if __name__ == '__main__':
    unittest.main()
