#!/usr/bin/env python3
"""Real Codex OAuth tool probes; never use API credentials or change model IDs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def probe(binary: str, model: str) -> dict:
    auth_path = Path.home() / ".codex/auth.json"
    before = auth_path.read_bytes()
    auth = json.loads(before)
    if auth.get("auth_mode") != "chatgpt" or not (auth.get("tokens") or {}).get("access_token") or auth.get("OPENAI_API_KEY"):
        raise RuntimeError("A ChatGPT OAuth login (not an API key) is required")
    with tempfile.TemporaryDirectory(prefix="codex-oauth-probe-") as temp:
        root = Path(temp)
        home = root / ".codex"
        home.mkdir(mode=0o700)
        (home / "auth.json").write_bytes(before)
        (home / "auth.json").chmod(0o600)
        (home / "config.toml").write_text(
            f'model = {json.dumps(model)}\nmodel_provider = "openai"\n'
            'model_reasoning_effort = "high"\napproval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n')
        work = root / "work"
        work.mkdir()
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in
               ("API_KEY", "TOKEN", "SECRET", "ANTHROPIC", "OPENAI", "CODEX", "PRIME"))}
        env.update(HOME=str(root), CODEX_HOME=str(home), NO_COLOR="1")
        result = subprocess.run([binary, "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
                                 "--color", "never", "--json", "Use your shell tool to execute exactly: printf CODEX_OAUTH_READY. Then reply READY and stop. Do not read files or do other work."],
                                cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
        # Preserve a legitimate OAuth refresh for later seats, but never overwrite
        # a concurrent external login change. No credential enters the report.
        after = (home / "auth.json").read_bytes()
        if after != before:
            if auth_path.read_bytes() != before:
                raise RuntimeError("OAuth auth store changed concurrently; refusing to overwrite")
            renewed = json.loads(after)
            if renewed.get("auth_mode") != "chatgpt" or renewed.get("OPENAI_API_KEY"):
                raise RuntimeError("Unexpected auth mode after probe")
            replacement = auth_path.with_name(f".auth-refresh-{os.getpid()}.json")
            fd = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(after)
            replacement.replace(auth_path)
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        tools = [e["item"] for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"]
        tool_ok = any(t.get("exit_code") == 0 and "CODEX_OAUTH_READY" in t.get("aggregated_output", "") for t in tools)
        models = set()
        for path in (home / "sessions").rglob("*.jsonl"):
            for line in path.read_text().splitlines():
                record = json.loads(line)
                if record.get("type") == "turn_context" and record.get("payload", {}).get("model"):
                    models.add(record["payload"]["model"])
        output = result.stdout[-10000:]
        for store in (auth, json.loads(after)):
            for value in (store.get("tokens") or {}).values():
                if isinstance(value, str) and value:
                    output = output.replace(value, "[REDACTED]")
        return {"model": model, "auth_mode": "chatgpt", "requested_models_in_native_store": sorted(models),
                "ok": result.returncode == 0 and tool_ok and models == {model} and any(e.get("type") == "turn.completed" for e in events),
                "exit_code": result.returncode, "output": output}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("models", nargs="+")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for model in args.models:
        result = probe(args.binary, model.rpartition("/")[2])
        results.append(result)
        args.out.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({k: result[k] for k in ("model", "auth_mode", "ok", "exit_code")}), flush=True)
    raise SystemExit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
