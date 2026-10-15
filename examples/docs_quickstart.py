import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORK = Path(tempfile.mkdtemp(prefix="agent-workflow-example-")).resolve()
REPO = WORK / "repo"
STATE = WORK / "state"
REPO.mkdir()
subprocess.run(["git", "init", "-q", str(REPO)], check=True, capture_output=True)
(REPO / "note.txt").write_text("before\n")
CONFIG = WORK / "project.json"
CONFIG.write_text(json.dumps({
    "repo": str(REPO),
    "tests": [[sys.executable, "-c", "from pathlib import Path; assert Path('note.txt').read_text() == 'after\\n'"]],
    "sources": ["local"],
    "budget": {"lease_seconds": 120, "run_seconds": 120, "test_seconds": 30, "max_attempts": 1},
    "acceptance": {"require_tests": True, "require_unchanged_candidate": True},
    "permissions": {"execute_tests": True, "network": False, "publish": False},
}))
BASE = [sys.executable, "-B", "-m", "agent_workflow", "--state", str(STATE)]
actions = []

def call(action, args=None, initialize=False):
    command = BASE + (["--config", str(CONFIG)] if initialize else [])
    command += [action, "--args", json.dumps(args or {})]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, action
    response = json.loads(result.stdout)
    assert response["ok"], action
    actions.append({"action": action, "exit_code": result.returncode})
    return response["result"]

call("init", initialize=True)
task = call("import", {"external_key": "example:001", "title": "Local no-model example"})
claim_response = {"ok": True, "result": call("claim", {"worker": "local-example"})}
private_path = WORK / "claim.private.json"
fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    json.dump(claim_response, out)
run = json.loads(private_path.read_text())["result"]
owned = {"run_id": run["id"], "token": run["token"]}
call("progress", dict(owned, note="Local no-model example"))
(REPO / "note.txt").write_text("after\n")
call("change", owned)
assert call("verify", owned)["passed"] is True
call("transition", dict(owned, target="ready_review"))
status = call("status", {"run_id": run["id"]})
assert status["state"] == "ready_review"
assert private_path.stat().st_mode & 0o777 == 0o600
print(json.dumps({"passed": True, "state": status["state"], "published": False,
                  "actions": actions, "private_handle": str(private_path), "model_calls": 0, "token_printed": False}))
