"""Inspect only the specified test Run in the Linux runtime, without secret output."""

import json
import sqlite3
import sys

from backend.config.settings import load_settings


settings = load_settings()
path = settings.application["run_store_path"]
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
    row = db.execute("SELECT body FROM run_sessions WHERE id=?", (sys.argv[1],)).fetchone()
if row is None:
    raise RuntimeError("test_session_missing")
run = next(item for item in json.loads(row[0])["snapshot"]["runs"] if item["run_id"] == sys.argv[2])
state = run["execution_state"]
receipts = state.get("tool_results", [])
command_by_action = {
    action["action_id"]: action.get("arguments", {}).get("command")
    for record in state.get("records", [])
    if record.get("kind") == "decision"
    for action in record.get("body", {}).get("proposal", {}).get("action_requests", [])
    if action.get("tool_name") == "bash"
}
print(json.dumps({
    "run_id": run["run_id"],
    "status": run["status"],
    "skills": [
        {key: item["result"][key] for key in ("skill_id", "version", "package_path", "resource")}
        for item in receipts if item["tool_name"] == "read_skill" and item["outcome"] == "ok"
    ],
    "commands": [
        {"outcome": item["outcome"], "exit_code": item["result"].get("exit_code"),
         "command": item["result"].get("command") or command_by_action.get(item["action_id"]),
         "stderr": item["result"].get("stderr"),
         "stdout": item["result"].get("stdout")}
        for item in receipts if item["tool_name"] == "bash"
    ],
}, ensure_ascii=False))
