"""Runs inside the Colab kernel, uploaded by launch.py. Three jobs, chosen by $RUU_DO:

  setup   make a separate Python environment for the engine, so it cannot fight the runtime's own packages
  run     run worker.py in that environment on the runs named in $RUU_JOBS, passing its progress lines through
  tune    run tune.py on the training file of the tune named in $RUU_TUNE

The engine's own log is long; it goes to a file on the VM and only the tail is shown on failure.
"""
import os
import shutil
import subprocess
import sys
import threading
import time

ENV = "/content/engine"


def setup():
    started = time.time()
    packages = os.environ.get("RUU_PACKAGES", "vllm").split()
    if os.path.exists(ENV + "/ready"):  # a retried setup finds the work already done
        print("[launch] engine environment: exit 0 in 0s (already there)", flush=True)
        return
    done = subprocess.run(f"rm -rf {ENV} && uv venv -q {ENV} --python 3.12 && uv pip install -q --python {ENV}/bin/python "
                          + " ".join(packages) + " --torch-backend=auto", shell=True, capture_output=True, text=True)
    if done.returncode != 0:
        print(f"[launch] engine environment: exit {done.returncode} in {time.time() - started:.0f}s", flush=True)
        print((done.stdout + done.stderr)[-1500:], flush=True)
        raise SystemExit(1)
    open(ENV + "/ready", "w").close()
    shown = subprocess.run([f"{ENV}/bin/python", "-c", "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__)"],
                           capture_output=True, text=True)
    print("[launch] " + (shown.stdout.strip().splitlines() or ["engine versions unreadable"])[-1], flush=True)
    gpu = subprocess.run("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", shell=True, capture_output=True, text=True)
    print("[launch] gpu: " + gpu.stdout.strip(), flush=True)
    print(f"[launch] engine environment: exit 0 in {time.time() - started:.0f}s", flush=True)


def stream(command, log_path, prefix, what):
    """Run a long program, keep its whole log on the VM and pass on only its progress lines."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    alive = threading.Event()

    def heartbeat():  # model download and engine start-up are quiet for minutes
        began = time.time()
        while not alive.wait(120):
            print(f"[launch] still working, {time.time() - began:.0f}s", flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    with open(log_path, "w", encoding="utf-8") as log:
        for line in proc.stdout:
            log.write(line)
            if line.startswith(prefix):
                print(line, end="", flush=True)
    code = proc.wait()
    alive.set()
    if code != 0:
        with open(log_path, encoding="utf-8") as log:
            print("".join(log.readlines()[-30:])[-3000:], flush=True)
        print(f"[launch] {what} exited {code}", flush=True)
        raise SystemExit(1)


def filed(name, folder, files):
    """Files arrive flat as /content/<name>.<file>; give each run or tune its own folder."""
    os.makedirs(folder, exist_ok=True)
    for file in files:
        if os.path.exists(f"/content/{name}.{file}"):
            shutil.move(f"/content/{name}.{file}", f"{folder}/{file}")
    return folder


def run():
    jobs = [filed(run_id, f"/content/jobs/{run_id}", ("spec.json", "prompts.jsonl")) for run_id in os.environ["RUU_JOBS"].split()]
    stream([f"{ENV}/bin/python", "-u", "/content/worker.py", *jobs], os.path.join(jobs[0], "engine.log"), "[worker]", "worker")


def tune():
    """Train in the runtime's own Python, which already has torch for this GPU; the engine keeps its separate one."""
    tune_id = os.environ["RUU_TUNE"]
    src, out = filed(tune_id, f"/content/tunes/{tune_id}", ("tune.json", "train.jsonl")), f"/content/tuned/{tune_id}"
    if os.path.exists(out + "/tune_meta.json"):  # a retried step finds the work already done
        print("[tune] all done (already there)", flush=True)
        return
    shutil.rmtree(out, ignore_errors=True)
    wanted = [p for p in os.environ.get("RUU_TUNE_PACKAGES", "peft").split()
              if subprocess.run([sys.executable, "-c", f"import {p.replace('-', '_')}"], capture_output=True).returncode != 0]
    if wanted:
        done = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *wanted], capture_output=True, text=True)
        if done.returncode != 0:
            print((done.stdout + done.stderr)[-1500:], flush=True)
            print(f"[launch] could not install {' '.join(wanted)}", flush=True)
            raise SystemExit(1)
    stream([sys.executable, "-u", "/content/tune.py", src, out], src + "/tune.log", "[tune]", "trainer")


if os.environ.get("RUU_DO") == "setup":
    setup()
elif os.environ.get("RUU_DO") == "run":
    run()
elif os.environ.get("RUU_DO") == "tune":
    tune()
else:
    print("[launch] RUU_DO is not set", flush=True)
    sys.exit(1)
