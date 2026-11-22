"""Command-line entry: ``python -m ntfy_bridge <command>``.

Commands:
    serve      run the daemon (systemd)
    once       process the backlog and every queued job, then exit
    doctor     validate configuration and runtime prerequisites
    onfailure  publish a "bridge stopped" notice (systemd OnFailure)
    resend ID  re-publish a stored report attachment
    status     print queue/counter state (no network)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow `python ntfy_bridge/__main__.py` as well as `python -m ntfy_bridge`:
# the former has no package context, so put the repo root on sys.path first.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntfy_bridge import __version__  # noqa: E402
from ntfy_bridge.client import NtfyClient, NtfyError  # noqa: E402
from ntfy_bridge.config import Config  # noqa: E402
from ntfy_bridge.jobs import Store  # noqa: E402
from ntfy_bridge.runner import git_state  # noqa: E402
from ntfy_bridge.service import Service  # noqa: E402


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("NTFY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _build(args: argparse.Namespace) -> tuple[Config, Service, Store]:
    config = Config.from_env()
    store = Store(config.home)
    service = Service(config, store=store, client=NtfyClient(config.server, config.token))
    return config, service, store


def cmd_serve(args: argparse.Namespace) -> int:
    config, service, _store = _build(args)
    service.serve_forever()
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    _config, service, _store = _build(args)
    return service.run_once()


def cmd_status(args: argparse.Namespace) -> int:
    _config, service, store = _build(args)
    print(service.status_text())
    jobs = store.load_jobs()
    running = [job for job in jobs if job.get("status") == "running"]
    for job in running:
        print(f"running: {job['id']} {job['ticker']} {job['trade_date']}")
    return 0


def cmd_resend(args: argparse.Namespace) -> int:
    _config, service, _store = _build(args)
    service.resend(args.job_id)
    return 0


def cmd_onfailure(args: argparse.Namespace) -> int:
    config, service, _store = _build(args)
    service.notify_text(
        "TradingAgents ntfy bridge 已停止（systemd OnFailure）。"
        "请查看 `journalctl -u ntfy-bridge` 并检查服务状态。",
        title="Bridge 已停止",
        tags=("rotating_light", "warning"),
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = Config.from_env()
    print(f"ntfy_bridge {__version__}")
    print(config.describe())
    problems = list(config.problems())
    if not Path(config.cli).exists() and not _which(config.cli):
        problems.append(f"找不到 CLI 可执行文件: {config.cli}")
    try:
        config.home.mkdir(parents=True, exist_ok=True)
        probe = config.home / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        problems.append(f"状态目录不可写: {config.home}: {exc}")
    branch, dev = git_state(config)
    print(f"git             {branch or '(none)'} | dev-build={dev}")
    if dev:
        print("提示: 当前是 dev-build，结果通知会带 construction 标签。")
    if args.send:
        service = Service(config, store=Store(config.home))
        ok = service.notify_text(
            "doctor: 这是一条测试消息。",
            title="ntfy bridge 自检",
            tags=("white_check_mark",),
        )
        print(f"test message    {'sent' if ok else 'deferred to outbox (network?)'}")
    if problems:
        print("\n发现以下问题:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\n配置检查通过。")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Read-only connectivity/auth check: poll the command topic once."""
    config = Config.from_env()
    client = NtfyClient(config.server, config.token)
    try:
        events = client.poll(config.command_topic, since="1m", timeout=15)
    except NtfyError as exc:
        print(f"订阅检查失败: {exc}")
        return 1
    print(f"订阅检查通过（读取 {len(events)} 条缓存事件）")
    return 0


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ntfy_bridge", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ntfy_bridge {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the daemon")
    serve.set_defaults(func=cmd_serve)

    once = sub.add_parser("once", help="drain backlog and queue, then exit")
    once.set_defaults(func=cmd_once)

    doctor = sub.add_parser("doctor", help="validate configuration")
    doctor.add_argument("--send", action="store_true", help="also send a test notification")
    doctor.set_defaults(func=cmd_doctor)

    check = sub.add_parser("check", help="read-only subscribe/auth check")
    check.set_defaults(func=cmd_check)

    onfailure = sub.add_parser("onfailure", help="publish a stopped notice")
    onfailure.set_defaults(func=cmd_onfailure)

    resend = sub.add_parser("resend", help="re-publish a stored report")
    resend.add_argument("job_id")
    resend.set_defaults(func=cmd_resend)

    status = sub.add_parser("status", help="print local queue state")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return int(args.func(args))
    except SystemExit:
        raise
    except Exception as exc:  # last-resort: never die silently
        logging.getLogger("ntfy_bridge").exception("command failed")
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
