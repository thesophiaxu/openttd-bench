import json
import sys
from scripts import watch_run


def test_mixed_progress_includes_names_and_loans(tmp_path):
    row = {"at": 100, "spec": "mixed_hogex_realtime_10y_800k_noloans-s101", "date": "1951-01-01",
           "elapsed": 100, "companies": {"0": {"operating_profit": 42, "cash": 800000,
           "vehicles": {"rail": 1}, "loan": 0, "max_loan": 0}}}
    (tmp_path / "progress.jsonl").write_text(json.dumps(row) + "\n")
    text = watch_run.latest_progress(tmp_path, 0)
    assert "Astra" in text and "loan $0 (cap $0)" in text
    (tmp_path / "progress.jsonl").write_text('{"at":')
    assert watch_run.latest_progress(tmp_path, 0) is None


def test_native_companies_are_included():
    episode = {"ok": True, "traces": [{"info": {"openttd_company": 0,
        "openttd_final": {"companies": {"2": {"name": "AAAHogEx", "operating_profit": 123,
        "vehicles": {"rail": 1}, "loan": 0}}}}}]}
    assert "AAAHogEx/c2: profit $123" in watch_run.episode_summary(episode)


def test_explicit_four_model_labels(tmp_path):
    row = {"at": 100, "companies": {str(i): {"operating_profit": i} for i in range(4)}}
    (tmp_path / "progress.jsonl").write_text(json.dumps(row) + "\n")
    labels = ["GPT-6 Astra", "GPT-5.6 Sol", "GPT-5.5", "GPT-5.4"]
    text = watch_run.latest_progress(tmp_path, 0, labels)
    assert all(label in text for label in labels)
    assert "HogEx" not in text and "Opus" not in text


def test_failed_manifest_is_not_announced_as_success(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text('{"status":"failed"}')
    sent = []
    monkeypatch.setattr(watch_run, "post", lambda topic, title, body: sent.append((title, body)))
    monkeypatch.setattr(watch_run.os.path, "exists", lambda path: False)
    monkeypatch.setattr(sys, "argv", ["watch_run", str(tmp_path), "--topic", "test", "--pid", "987654"])
    assert watch_run.main() == 1
    assert "FAILED / incomplete" in sent[-1][0]
