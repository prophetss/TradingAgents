# ntfy bridge

Run TradingAgents from your phone: send a ticker to an [ntfy](https://ntfy.sh)
topic, the analysis runs on this machine, and the markdown report comes back as
an ntfy attachment.

```
phone ──publish(command)──► ntfy command topic
                                   │
                            ntfy_bridge daemon
                                   │ subprocess
                            tradingagents CLI (headless)
                                   │ writes state log + complete_report.md
                            ntfy_bridge daemon
                                   │ PUT attachment + summary
phone ◄──notification+file── ntfy result topic
```

## Why it lives in its own directory

`ntfy_bridge/` is a standalone package. It never imports `tradingagents` or
`cli`, and no existing file was changed to add it. It depends only on:

1. the CLI's headless flags,
2. the on-disk artifacts (`full_states_log_<date>.json`,
   `reports/<ticker>_<stamp>/complete_report.md`),
3. the `TRADINGAGENTS_*` environment variables.

Upstream merges therefore cannot conflict with it, and this module does not need
to change when upstream refactors internals.

## Requirements

- Linux with systemd (the daemon also runs fine in tmux/`nohup`)
- the project's existing `.venv` (the CLI must run headlessly)
- `requests` (already a dependency) and Python 3.11+
- an ntfy app on the phone

No new Python packages.

## Setup

```bash
cd /path/to/TradingAgents-akshare

# 1. settings
install -m 700 -d ~/.tradingagents/ntfy-bridge
install -m 600 ntfy_bridge/systemd/env.example ~/.tradingagents/ntfy-bridge/env
$EDITOR ~/.tradingagents/ntfy-bridge/env       # topics + secret + language

# 2. validate (add --send to also publish a test notification)
.venv/bin/python -m ntfy_bridge doctor
.venv/bin/python -m ntfy_bridge check          # read-only subscribe/auth check

# 3. run in the foreground once to see it work
.venv/bin/python -m ntfy_bridge serve
```

`python ntfy_bridge/__main__.py ...` works too, if the editable install does not
expose the new top-level package.

### systemd

```bash
sed "s|<REPO>|$PWD|g" ntfy_bridge/systemd/ntfy-bridge.service \
  > ~/.config/systemd/user/ntfy-bridge.service
sed "s|<REPO>|$PWD|g" ntfy_bridge/systemd/ntfy-bridge-failure.service \
  > ~/.config/systemd/user/ntfy-bridge-failure.service

systemctl --user daemon-reload
systemctl --user enable --now ntfy-bridge.service
journalctl --user -u ntfy-bridge -f
```

The unit is `Type=notify` with `WatchdogSec=60`; the daemon sends `READY=1` and
`WATCHDOG=1` itself. `OnFailure=` publishes a "bridge stopped" message using the
same env file (mode 600 — it holds your token).

## Using it from the phone

In the ntfy app:

1. subscribe to **result topic** (notifications + attachments),
2. publish plain-text messages to **command topic**.

Commands (Chinese or English both fine; the response language follows
`NTFY_OUTPUT_LANGUAGE` or `language=`):

```text
600519.SH                              # today, all analysts
600519.SH 2026-11-20                   # specific date
600519.SH 2026-11-20 market,news       # specific analysts
ticker=AAPL date=2026-11-20 analysts=market language=Chinese
rerun 600519.SH 2026-11-20             # bypass the same-day duplicate guard
resend 20261120-120000-ab12cd          # re-send a stored report (no re-run)
status                                 # queue and today's counters
help                                   # usage (rate-limited when unsigned)
```

With `NTFY_COMMAND_SECRET` set, prefix every command with it:

```text
<secret> 600519.SH 2026-11-20
# or
600519.SH 2026-11-20 secret=<secret>
```

A run takes 2–10 minutes. You get: `已排队` → `开始分析` → result notification
with the rating, a short decision summary, and `complete_report.md` attached.

## What failures look like

| Situation | What the phone gets |
|---|---|
| Bad command (signed) | immediate ⛔ message with the reason/usage |
| Missing/wrong secret | silence (one generic rejection per 10 min; counted in the heartbeat) |
| Queue full / daily budget / duplicate | immediate ⛔ message |
| CLI exit ≠ 0, crash, timeout (30 min) | ⚠️ message with exit code, scrubbed log tail, local job id |
| Rate-limited data source (East Money) | job marked failed; bridge pauses its own jobs for 10 min |
| ntfy temporarily unreachable | notification persisted to `outbox/`, retried every 30 s, flushed on restart |
| Permanent ntfy rejection (400/413) | message abandoned to `dead-letter/`, counted in the heartbeat |
| Subscription silently dead | daemon notices >3 missed polls, says so on the result topic, keeps retrying |
| Daemon crash/hang | systemd restarts it; `OnFailure` publishes "bridge stopped" |
| Machine off / no network | **no message** — the missing daily heartbeat is the signal |

Deliberate degradations:

- ntfy caches messages ~12 h: commands sent while the machine is off longer than
  that are lost. The bridge reports "N commands expired" for anything it can see,
  and reports the downtime gap when it starts.
- ntfy.sh deletes attachments after ~3 h: the summary text stays readable, and
  `resend <job id>` re-sends the file from local storage (kept
  `NTFY_RETENTION_DAYS`, default 14).
- On ntfy.sh, the result topic is readable and writable by anyone who knows its
  name. Result notifications carry a title prefix (`NTFY_TITLE_PREFIX`) you can
  verify. For confidentiality/integrity, self-host ntfy with ACLs and set
  `NTFY_TOKEN`.

## Operations

```bash
.venv/bin/python -m ntfy_bridge status     # queue, today's counters, outbox
.venv/bin/python -m ntfy_bridge once       # drain backlog + queue, then exit
.venv/bin/python -m ntfy_bridge resend ID  # re-send a stored report
journalctl --user -u ntfy-bridge -f
```

State lives outside the repo:

```
~/.tradingagents/ntfy-bridge/
  queue.json          persisted jobs (atomic writes)
  seen.json           last processed ntfy message id
  stats.json          today's counters + heartbeat marker
  jobs/<id>/          log.txt, results/ (state log + report tree), meta
  outbox/             notifications waiting for ntfy
  dead-letter/        permanently failed notifications
```

Each job gets its own `TRADINGAGENTS_RESULTS_DIR`
(`jobs/<id>/results`), so bridge runs never mix with your interactive runs.

## Development

```bash
.venv/bin/python -m pytest ntfy_bridge/tests -q
.venv/bin/python -m ruff check ntfy_bridge/
```

The tests never touch the network, the real home directory, or the real CLI:
the runner is exercised against a stub executable that writes the same
artifact layout.

## Security notes

- Topics are passwords. Use long random names; never reuse one across bridges.
- `NTFY_COMMAND_SECRET` protects execution and LLM spend, but it travels in the
  message body and remains in ntfy's cache. Anyone who learns the command topic
  can read it from history. For anything beyond hobby use, self-host ntfy with
  authentication and ACLs, and set `NTFY_TOKEN`.
- The bridge strips every `NTFY_*` variable from the child environment, so the
  LLM agents (which read untrusted web content) cannot be prompt-injected into
  leaking the token or secret into a report.
- Reports are scrubbed for known secrets before upload; the local copy is kept
  untouched.
- Daily job limit, queue limit, and same-day duplicate protection bound LLM
  spend. Commands are lexical-only validated here; the CLI is the authority on
  ticker/analyst semantics.
