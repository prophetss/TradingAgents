from __future__ import annotations

from ntfy_bridge.config import Config
from ntfy_bridge.runner import build_argv, build_env, run_job

SUCCESS_SCRIPT = '''#!/usr/bin/env python3
import json, os, sys
if "NTFY_TOKEN" in os.environ or "NTFY_COMMAND_SECRET" in os.environ:
    sys.exit(9)
results = os.environ["TRADINGAGENTS_RESULTS_DIR"]
ticker = sys.argv[sys.argv.index("--ticker") + 1]
trade_date = sys.argv[sys.argv.index("--date") + 1]
logs = os.path.join(results, ticker, "TradingAgentsStrategy_logs")
os.makedirs(logs, exist_ok=True)
with open(os.path.join(logs, "full_states_log_%s.json" % trade_date), "w") as handle:
    json.dump({"final_rating": "Hold",
               "final_trade_decision": "决策正文 NO_DATA_AVAILABLE"}, handle)
reports = os.path.join(results, "reports", ticker + "_20261120_000000")
os.makedirs(reports, exist_ok=True)
with open(os.path.join(reports, "complete_report.md"), "w") as handle:
    handle.write("# 报告\\n正文")
'''

EMPTY_SCRIPT = '''#!/usr/bin/env python3
import sys
sys.exit(0)
'''

SLOW_SCRIPT = '''#!/usr/bin/env python3
import time
time.sleep(30)
'''


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _config(tmp_path, cli, *, timeout=30):
    return Config(
        server="https://ntfy.example",
        command_topic="cmd",
        result_topic="result",
        home=tmp_path / "home",
        repo_root=tmp_path,
        cli=str(cli),
        job_timeout=timeout,
    )


def _job():
    return {
        "id": "20261120-120000-ab12cd",
        "ticker": "600519.SH",
        "trade_date": "2026-11-20",
        "analysts": ["market", "news"],
        "language": "Chinese",
    }


def test_argv_is_headless_and_fresh(tmp_path):
    argv = build_argv(_config(tmp_path, tmp_path / "cli"), _job())
    assert argv[1:] == [
        "--ticker", "600519.SH",
        "--date", "2026-11-20",
        "--analysts", "market,news",
        "--save", "--no-show", "--no-html", "--clear-checkpoints",
    ]


def test_child_env_strips_bridge_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    monkeypatch.setenv("NTFY_COMMAND_SECRET", "hunter2")
    env = build_env(_config(tmp_path, tmp_path / "cli"), _job(), tmp_path / "results")
    assert not any(key.startswith("NTFY_") for key in env)
    assert env["TRADINGAGENTS_RESULTS_DIR"] == str(tmp_path / "results")
    assert env["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "Chinese"


def test_success_reads_rating_and_report(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    cli = _script(tmp_path, "cli.py", SUCCESS_SCRIPT)
    result = run_job(_job(), _config(tmp_path, cli))

    assert result.ok
    assert result.rating == "Hold"
    assert result.report_path and result.report_path.name == "complete_report.md"
    assert result.state_path and result.state_path.name.endswith("2026-11-20.json")
    assert result.degraded is True  # NO_DATA_AVAILABLE in the decision
    assert result.log_path and result.log_path.is_file()


def test_exit_zero_without_artifacts_is_an_error(tmp_path):
    cli = _script(tmp_path, "cli.py", EMPTY_SCRIPT)
    result = run_job(_job(), _config(tmp_path, cli))
    assert not result.ok
    assert "未找到报告" in result.error


def test_timeout_kills_the_process_group(tmp_path):
    cli = _script(tmp_path, "slow.py", SLOW_SCRIPT)
    result = run_job(_job(), _config(tmp_path, cli, timeout=1))
    assert result.timed_out
    assert "超时" in result.error
    assert result.duration < 15
