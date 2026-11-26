"""Exercise a build receipt and the working material produced by the next resident turn."""

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


LAB = Path(__file__).resolve().parents[2] / "ruu-lab"


class WorkingPreferencesTest(unittest.TestCase):
    def test_build_updates_next_material_and_rejects_stale_or_failed_changes(self):
        self.enterContext(patch.object(sys, "path", [str(LAB), *sys.path]))
        spec = importlib.util.spec_from_file_location("preference_resident", LAB / "soft_machine.py")
        resident = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resident)
        import working_preferences
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(resident, "STATE", state))
        self.enterContext(patch.object(resident, "OPENRSI_TARGETS", state / "absent"))
        self.enterContext(patch.object(resident, "Typing"))
        self.enterContext(patch.object(resident, "post_run_log"))
        self.enterContext(patch.object(resident, "log"))
        db = self.enterContext(closing(resident.open_db()))
        name = "working-preferences.json"
        sandbox = {"ok": True, "exit": 0, "written": ["app.py", name], "stdout": "complete output",
                   "workspace": [{"path": p, "bytes": 10} for p in ("app.py", name)]}
        self.enterContext(patch.object(resident, "hands_run", return_value=sandbox))
        drives = [{"title": "Research", "brief": "Study"}, {"title": "Art", "brief": "Draw"}]
        cfg = {"lab_channel_id": "test", "drives": drives}
        changes = {"workspace_files": 1, "stdout_chars": 6, "last_code_chars": 4, "next_drive": "Art"}

        def build(update):
            files = [{"path": "app.py", "content": "print(42)"},
                     {"path": name, "content": json.dumps(update)}]
            resident.use_hands(None, db, cfg, {"id": 1, "title": "", "brief": ""},
                               {"files": files, "command": "python app.py"})

        before = resident.hands_block(db, drives=drives)["working_preferences"]
        self.assertEqual(before["available_drives"], ["Research", "Art"])
        self.assertEqual(before["values"], {"workspace_files": 20, "stdout_chars": 1500,
                                           "last_code_chars": 5000, "next_drive": None})
        self.assertIsNone(before["last_receipt"])
        build({"base_revision": 0, "set": changes})
        with closing(resident.open_db()) as reader:
            material = resident.hands_block(reader)
        self.assertEqual(material["working_preferences"]["values"], changes)
        receipt = material["working_preferences"]["last_receipt"]
        self.assertEqual(receipt, {"outcome": "applied", "job": 1, "revision": 1, "changed": changes})
        self.assertEqual(material["workspace"]["files"], ["app.py (10 b)"])
        self.assertEqual(material["last_run"]["stdout"], "output")
        self.assertEqual(material["last_code"], [{"path": "app.py", "content": "prin"}])
        self.assertNotIn("last_code", resident.hands_block(db, with_code=False))

        for base, values, success, reason in [
            (0, {"stdout_chars": 3}, True, "stale base_revision"),
            (1, {"stdout_chars": 1501}, True, "integer from 0 to 1500"),
            (1, {"next_drive": "Unconfigured"}, True, "available drive title"),
            (1, {"stdout_chars": 0}, False, "exit successfully"),
        ]:
            sandbox["exit"] = 0 if success else 1
            build({"base_revision": base, "set": values})
            prefs = resident.hands_block(db)["working_preferences"]
            self.assertEqual((prefs["revision"], prefs["values"], prefs["last_receipt"]["outcome"]),
                             (1, changes, "rejected"))
            self.assertIn(reason, prefs["last_receipt"]["reason"])
        self.assertEqual(resident.kv_get(db, "working_preferences.receipt.1", None), receipt)

        resident.issue_order(db, "above", "Existing order", "", [])
        now = 100000.0
        self.assertIsNone(working_preferences.issue_drive_if_due(db, cfg, now))
        with db:
            db.execute("update orders set status='closed' where source='above'")
        self.assertIsNone(working_preferences.issue_drive_if_due(db, {**cfg, "drives_daily_max": 0}, now))
        self.assertEqual(resident.hands_block(db)["working_preferences"]["values"]["next_drive"], "Art")
        self.assertEqual(working_preferences.issue_drive_if_due(db, cfg, now), "Art")
        with closing(resident.open_db()) as reader:
            order = resident.open_order(reader)
            prefs = resident.hands_block(reader)["working_preferences"]
        self.assertEqual((order["source"], order["title"], order["brief"], order["step"]), ("drive", "Art", "Draw", 1))
        self.assertEqual(prefs["last_drive"], {"job": 1, "order_id": order["id"], "outcome": "consumed",
                                              "title": "Art", "revision": 2})
        self.assertEqual(prefs["values"], {**changes, "next_drive": None})
        with db:
            db.execute("update orders set status='closed' where id=?", (order["id"],))
        self.assertEqual(working_preferences.issue_drive_if_due(db, cfg, now), "Research")
