"""Resident choices about working material and the next standing drive."""

import json


FILE = "working-preferences.json"
KEY = "working_preferences"
LIMITS = {"workspace_files": 20, "stdout_chars": 1500, "last_code_chars": 5000}


def current(db):
    row = db.execute("select value from kv where key=?", (KEY,)).fetchone()
    state = json.loads(row[0]) if row else {"revision": 0, "values": LIMITS.copy(), "last_receipt": None}
    state["values"].setdefault("next_drive", None)
    return state


def available_drives(drives):
    titles = [drive["title"] for drive in drives if isinstance(drive.get("title"), str)]
    return [title for title in titles if title and len(title) <= 200 and title == title.strip()
            and titles.count(title) == 1]


def describe(state, drives):
    return {**state, "update": (
        'Submit working-preferences.json in files with base_revision equal to revision and a set object. '
        'Set context limits to integers from 0 through their maximums. Set next_drive to an available title '
        'to take it once when free, or null to cancel. A written file and a successful command apply the change.'
    ), "maximums": LIMITS, "available_drives": available_drives(drives)}


def _unique_fields(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate field")
        obj[key] = value
    return obj


def _parse(content, drives):
    if len(content) > 1024 or len(content.encode("utf-8", "surrogatepass")) > 1024:
        raise ValueError("limit is 1024 bytes")
    try:
        update = json.loads(content, object_pairs_hook=_unique_fields)
    except (ValueError, RecursionError):
        raise ValueError("expected JSON with unique fields") from None
    if not isinstance(update, dict) or update.keys() != {"base_revision", "set"}:
        raise ValueError("expected base_revision and set")
    revision, changes = update["base_revision"], update["set"]
    if type(revision) is not int or revision < 0:
        raise ValueError("base_revision must be a nonnegative integer")
    if not isinstance(changes, dict) or not changes or not changes.keys() <= LIMITS.keys() | {"next_drive"}:
        raise ValueError("set must contain one or more supported settings")
    for key, value in changes.items():
        if key == "next_drive":
            if value is not None and value not in available_drives(drives):
                raise ValueError("next_drive must be an available drive title or null")
        elif type(value) is not int or not 0 <= value <= LIMITS[key]:
            raise ValueError(f"{key} must be an integer from 0 to {LIMITS[key]}")
    return revision, changes


def _put(db, key, value):
    db.execute("insert into kv(key,value) values(?,?) on conflict(key) do update set value=excluded.value",
               (key, json.dumps(value)))


def record(db, job_id, files, result, drives):
    """Called under the write lock acquired by the uncommitted jobs INSERT."""
    candidates = [item["content"] for item in files if item["path"] == FILE]
    if not candidates:
        return
    state = current(db)
    receipt = {"job": job_id}
    try:
        if len(candidates) != 1:
            raise ValueError("submit the file only once")
        if (result.get("ok") is not True or result.get("exit") != 0 or result.get("timed_out")
                or FILE not in (result.get("written") or [])):
            raise ValueError("the build must write the file and exit successfully")
        revision, changes = _parse(candidates[0], drives)
        if revision != state["revision"]:
            raise ValueError("stale base_revision")
        changed = {key: value for key, value in changes.items() if value != state["values"][key]}
        if changed:
            state["values"].update(changed)
            if "next_drive" in changed:
                state["drive_request_job"] = job_id if changed["next_drive"] else None
            state["revision"] += 1
            receipt.update(outcome="applied", changed=changed)
        else:
            receipt["outcome"] = "unchanged"
    except ValueError as exc:
        receipt.update(outcome="rejected", reason=str(exc))
    receipt["revision"] = state["revision"]
    state["last_receipt"] = receipt
    db.execute("insert into kv(key,value) values(?,?)", (f"{KEY}.receipt.{job_id}", json.dumps(receipt)))
    _put(db, KEY, state)


def issue_drive_if_due(db, cfg, now):
    """Insert the next eligible order and consume its one-use preference in one transaction."""
    drives = cfg.get("drives") or []
    open_query = "select 1 from orders where status='open' limit 1"
    if not drives or db.execute(open_query).fetchone():
        return
    with db:
        if not db.in_transaction:
            db.execute("begin immediate")
        issued = db.execute("select count(*) from orders where source='drive' and ts>?", (now - 86400,)).fetchone()[0]
        if db.execute(open_query).fetchone() or issued >= cfg.get("drives_daily_max", 8):
            return
        row = db.execute("select value from kv where key='drive_index'").fetchone()
        index = json.loads(row[0]) if row else 0
        state = current(db)
        wanted = state["values"]["next_drive"]
        selected = wanted in available_drives(drives)
        if selected:
            position = next(i for i, drive in enumerate(drives) if drive["title"] == wanted)
            index += (position - index) % len(drives)
        drive = drives[index % len(drives)]
        cursor = db.execute("insert into orders(ts,source,title,brief,docs,step) values(?,'drive',?,?,'[]',1)",
                            (now, drive["title"].strip()[:200], drive["brief"].strip()[:600]))
        _put(db, "next_due", 0)
        _put(db, "drive_index", index + 1)
        if selected:
            state["values"]["next_drive"] = None
            state["revision"] += 1
            receipt = {"outcome": "consumed", "title": wanted, "order_id": cursor.lastrowid,
                       "job": state.pop("drive_request_job"), "revision": state["revision"]}
            state["last_drive"] = receipt
            _put(db, f"{KEY}.drive.{cursor.lastrowid}", receipt)
            _put(db, KEY, state)
    return drive["title"]


def visible_code(files, limit):
    visible = []
    for item in files:
        if limit <= 0:
            break
        content = item["content"][:limit]
        visible.append({"path": item["path"], "content": content})
        limit -= len(content)
    return visible
