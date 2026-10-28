#!/usr/bin/env python3
"""Bounded real Codex tool-use probes against the configured paid Prime endpoint."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "harnesses")]
from codex_prime import CodexPrimeHarness, CodexPrimeHarnessConfig
from openttd_seat import SeatPaths
from verifiers.v1 import ModelContext
from verifiers.v1.clients import EvalClientConfig


def probe(binary: str, model: str) -> dict:
    credentials = json.loads((Path.home() / ".prime/config.json").read_text())
    harness = CodexPrimeHarness(CodexPrimeHarnessConfig(id="codex-prime"))
    with tempfile.TemporaryDirectory(prefix="codex-prime-probe-") as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        paths = SeatPaths(user="probe", home=str(root), workdir=str(work), prompt_file=str(root / "prompt.md"))
        ctx = ModelContext(model=model, client=EvalClientConfig())
        for filename, content in harness.seat_files(ctx, paths, credentials["inference_url"], "").items():
            file = Path(filename)
            file.parent.mkdir(parents=True, exist_ok=True)
            # Team billing header is needed only for this direct preflight. In real
            # episodes the host interception client supplies provider authentication.
            content += ('\n[model_providers.prime_proxy.http_headers]\n"X-Prime-Team-ID" = '
                        + json.dumps(os.environ["PRIME_TEAM_ID"]) + '\n').encode()
            file.write_bytes(content)
            file.chmod(0o600)
        env = {k: v for k, v in os.environ.items()
               if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "ANTHROPIC", "OPENAI", "CODEX", "PRIME"))}
        env.update(HOME=str(root), CODEX_HOME=str(root / ".codex"), OPENAI_API_KEY=credentials["api_key"], NO_COLOR="1")
        prompt = "Use your shell tool to execute exactly: printf CODEX_PRIME_READY. Then reply READY and stop. Do not read files or do other work."
        result = subprocess.run([binary, "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "--color", "never", "--json", prompt],
                                cwd=work, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        tools = [e["item"] for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"]
        tool_ok = any(t.get("exit_code") == 0 and "CODEX_PRIME_READY" in t.get("aggregated_output", "") for t in tools)
        ok = result.returncode == 0 and tool_ok and any(e.get("type") == "turn.completed" for e in events)
        return {"model": model, "ok": ok, "exit_code": result.returncode,
                "output": result.stdout[-12000:].replace(credentials["api_key"], "[REDACTED]")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("models", nargs="+")
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda m: probe(args.binary, m), args.models))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps([{k: r[k] for k in ("model", "ok", "exit_code")} for r in results], indent=2))
    raise SystemExit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
