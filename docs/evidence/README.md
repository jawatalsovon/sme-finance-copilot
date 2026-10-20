# Execution evidence

Sandbox run of the whole approval path: reset → Cash Truth (cn_xborder) → chat question → refused transfer request → T2 draft queued → wrong-token and agent-actor approvals refused → owner approves with token → audit chain verified.

Everything is synthetic, uses the mock LLM and an in-memory database; no external system is contacted and no money can move.

## Files
- `SANDBOX_RUN_LOG.md` — step table and audit action sequence
- `SANDBOX_RUN_LOG.json` — full response of every step, including the complete audit log export (last entry)

## Re-run (from the repo root)

PowerShell:
```powershell
.\.venv\Scripts\python eval\demo_run.py
```
bash (Git Bash on Windows; on Linux/macOS use `.venv/bin/python`):
```bash
.venv/Scripts/python eval/demo_run.py
```
It runs in a few seconds. The owner token inside the script is a throw-away value for the in-memory app, not a secret.

Security tests: `pytest -q tests/security_unit` (65 tests).
