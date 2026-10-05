"""Modify only the notification validation database for browser transition checks."""
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".run/notification-validation/test.db"
assert path.is_file()
body = json.loads(sys.argv[1])
with sqlite3.connect(path) as db:
    if "id" in body:
        columns = {k: v for k, v in body.items() if k in {"state", "error", "result", "message"}}
        for key in ("result",):
            if key in columns: columns[key] = json.dumps(columns[key])
        columns["finished_at"] = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in columns) + " WHERE id=?",
                   [*columns.values(), body["id"]])
        print(body["id"])
    else:
        cur = db.execute(
            "INSERT INTO jobs(kind,state,progress,payload,result,error,attempts) VALUES(?,?,?,?,?,?,?)",
            (body.get("kind","scan_library"),body.get("state","done"),1,"{}",
             json.dumps(body.get("result")),body.get("error"),0),
        )
        print(cur.lastrowid)
