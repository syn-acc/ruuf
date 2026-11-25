"""The resident's compute planner, with a synthetic model decision and RCC process."""

import importlib.util
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


source = Path(__file__).resolve().parents[2] / "ruu-lab" / "soft_machine.py"
spec = importlib.util.spec_from_file_location("resident_under_test", source)
resident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resident)

PARAMS = dict(families=["algebra"], pool=10, pool_offset=0, samples=2,
              policy=["verified-short"], hard_only=True, seed=1, screen=5)
WHY = "Check the effect of selecting verified answers."
STATUS = {"ok": True, "kinds": {"colab": {}}, "running": [], "recent": []}


def response(body, exit_code=0):
    return subprocess.CompletedProcess([], exit_code, json.dumps(body).encode(), b"")


class ResidentRccTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(resident, "STATE", Path(directory)))
        self.db = self.enterContext(closing(resident.open_db()))
        self.cfg = {"models": [["test-model", "low", 1]], "legs": {"enabled": True}}
        decision = dict(go=True, why=WHY, kind="colab", **PARAMS)
        self.turn = self.enterContext(patch.object(resident, "turn", return_value=(decision, None, {}, 0)))
        self.rcc = self.enterContext(patch.object(resident.subprocess, "run"))
        self.enterContext(patch.object(resident, "log"))

    def test_dry_plan_preserves_refusal_without_reserving_a_run(self):
        refusal = {"ok": False, "refused": "daily limit reached"}

        def invoke(args, **kwargs):
            if args[3] == "status":
                return response(STATUS)
            self.assertEqual(args[3:6], ["plan", "--provider", "colab"])
            self.assertEqual(json.loads(Path(args[args.index("--params") + 1]).read_bytes()), PARAMS)
            self.assertFalse(resident.kv_get(self.db, "legs", {}).get("pending_run"))
            return response(refusal, exit_code=1)

        self.rcc.side_effect = invoke
        plan = resident.legs_plan(None, self.db, self.cfg, dry=True)
        self.assertEqual(plan["reply"], refusal)
        self.assertEqual([call.args[0][3] for call in self.rcc.call_args_list], ["status", "plan"])
        self.assertEqual(resident.kv_get(self.db, "legs", {}), {})

    def test_missing_paid_receipt_holds_across_restart_until_verified(self):
        reopened = self.enterContext(closing(resident.open_db()))

        def missing_receipt(args, **kwargs):
            if args[3] == "status":
                return response(STATUS)
            self.assertEqual(args[3], "run")
            self.assertEqual(resident.kv_get(reopened, "legs", {})["pending_run"]["params"], PARAMS)
            return response({"ok": True})  # The process exited successfully without a run ID.

        self.rcc.side_effect = missing_receipt
        self.assertTrue(resident.legs_tick(None, self.db, self.cfg))
        held = resident.kv_get(reopened, "legs", {})
        self.assertTrue(held["unresolved"])
        self.assertFalse(resident.legs_tick(None, reopened, self.cfg))
        self.assertEqual([call.args[0][3] for call in self.rcc.call_args_list], ["status", "run"])
        self.turn.assert_called_once()

        run_id = "real-receipt"
        started = time.strftime("%Y-%m-%d %H:%M", time.gmtime(held["pending_run"]["at"]))
        receipt = dict(id=run_id, kind="colab", why=WHY, params=PARAMS, started_utc=started)
        self.rcc.side_effect = [
            response({"ok": True, "stride": {**receipt, "params": {}}}),
            response({"ok": True, "stride": receipt}),
        ]
        with self.assertRaises(ValueError):
            resident.resolve_legs(reopened, self.cfg, run_id)
        self.assertEqual(resident.kv_get(reopened, "legs", {}), held)
        resident.resolve_legs(reopened, self.cfg, run_id)
        self.assertEqual(resident.kv_get(self.db, "legs", {}), {"stride": run_id, "check_at": 0})
        self.assertEqual([call.args[0][3:] for call in self.rcc.call_args_list[-2:]],
                         [["result", run_id], ["result", run_id]])
