"""The child system's worker: answer a file of prompts with one open model.

Runs on the GPU machine (a Colab runtime started by launch.py; any machine with the engine works).
It is given prompts and nothing else: no answers, no seeds, no way to compute a score.

    job/spec.json      what to run (written by launch.py)
    job/prompts.jsonl  {"id", "messages"} per line
    job/outputs.jsonl  {"id", "sample", "text", "tokens", "finish"} per line   <- written here
    job/meta.json      what actually ran: versions, device, time, tokens       <- written here

The job directory is $RUU_JOB or the first argument. Finished prompts are skipped on a rerun.
It prints a line per chunk; the launcher treats silence as a hang.

Two engines. `vllm` is the one that matters: measured 2026-11-20 on a Colab L4, Qwen3.5-4B ran at
about 900 tokens a second under vLLM and the 0.8B model at 137 under plain transformers, whose
kernels for this model family are slow reference code. `hf` stays for machines without a GPU.
"""
import json
import os
import sys
import time
from pathlib import Path


def write(sink, prompt_id, sample, text, tokens, finish):
    sink.write(json.dumps({"id": prompt_id, "sample": sample, "tokens": tokens, "finish": finish, "text": text},
                          ensure_ascii=False) + "\n")


ENGINES = {}  # one loaded engine per (model, revision): short runs should not each pay minutes of start-up


def open_vllm(spec, max_model_len):
    import vllm
    from vllm import LLM

    # A tuned child is either full weights in a folder on this machine (`weights`) or the base model
    # plus an adapter folder (`lora`). Both were made here by tune.py earlier in the same session.
    weights, lora = spec.get("weights"), spec.get("lora")
    for path in (weights, lora):
        if path and not os.path.isdir(path):
            raise SystemExit(f"[worker] {path} is not on this machine: a tuned child lives only in the session that made it")
    key = (weights or spec["model"], spec.get("revision"), bool(lora))
    if key not in ENGINES:
        started = time.time()
        options = {"model": weights or spec["model"], "revision": None if weights else (spec.get("revision") or None),
                   "dtype": "bfloat16", "seed": int(spec.get("seed", 0)), "max_model_len": max_model_len,
                   "gpu_memory_utilization": 0.90}
        if lora:
            options.update(enable_lora=True, max_loras=1, max_lora_rank=int(spec.get("lora_rank", 16)))
        try:  # text only: do not set memory aside for images a job never sends
            llm = LLM(**options, limit_mm_per_prompt={"image": 0, "video": 0})
        except (TypeError, ValueError):
            llm = LLM(**options)
        ENGINES[key] = (llm, time.time() - started)
        print(f"[worker] vLLM {vllm.__version__} loaded in {ENGINES[key][1]:.0f}s", flush=True)
    return ENGINES[key]


def run_vllm(spec, todo, out_path, max_model_len=8192):
    import torch
    import vllm
    from vllm import SamplingParams

    llm, load_seconds = open_vllm(spec, max_model_len)
    sampling = {"max_tokens": int(spec.get("max_new_tokens", 1024)), "n": int(spec.get("samples", 1)),
                "temperature": float(spec.get("temperature", 0.0)), "seed": int(spec.get("seed", 0))}
    if sampling["temperature"] > 0:
        sampling.update(top_p=float(spec.get("top_p", 1.0)), top_k=int(spec.get("top_k", 0)) or -1)
    else:
        sampling["n"] = 1
    for key in ("presence_penalty", "repetition_penalty"):
        if spec.get(key) is not None:
            sampling[key] = float(spec[key])
    params, template = SamplingParams(**sampling), spec.get("chat_template_kwargs") or {}
    extra = {}
    if spec.get("lora"):
        from vllm.lora.request import LoRARequest
        extra["lora_request"] = LoRARequest(str(spec.get("tuned") or "child"), 1, spec["lora"])
    generated, generating, chunk = 0, time.time(), int(spec.get("chunk", 512))
    with out_path.open("a", encoding="utf-8") as sink:
        for cursor in range(0, len(todo), chunk):
            batch = todo[cursor:cursor + chunk]
            results = llm.chat([p["messages"] for p in batch], params, chat_template_kwargs=template, use_tqdm=False,
                               **extra)
            for prompt, result in zip(batch, results):
                for sample, completion in enumerate(result.outputs):
                    tokens = len(completion.token_ids)
                    generated += tokens
                    write(sink, prompt["id"], sample, completion.text, tokens,
                          "length" if completion.finish_reason == "length" else "stop")
            sink.flush()
            rate = generated / max(1e-6, time.time() - generating)
            print(f"[worker] {min(cursor + chunk, len(todo))}/{len(todo)} prompts, {generated} tokens, {rate:.0f} tok/s",
                  flush=True)
    return {"engine": "vllm " + vllm.__version__, "torch": torch.__version__, "device": torch.cuda.get_device_name(0),
            "dtype": "bfloat16", "samples": sampling["n"], "tokens_generated": generated,
            "load_seconds": round(load_seconds, 1), "generate_seconds": round(time.time() - generating, 1)}


def run_hf(spec, todo, out_path):
    import torch
    import transformers
    from transformers import AutoTokenizer

    cuda = torch.cuda.is_available()
    if not cuda and spec.get("threads"):
        torch.set_num_threads(int(spec["threads"]))
    started = time.time()
    source = {"pretrained_model_name_or_path": spec["model"], "revision": spec.get("revision") or None}
    tokenizer = AutoTokenizer.from_pretrained(**source)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    dtype = torch.bfloat16 if cuda else torch.float32
    model, errors = None, []
    # Some chat models ship as multimodal classes, and the dtype argument was renamed between
    # transformers versions; try the combinations rather than pin a version on a machine we do not own.
    for name in ("AutoModelForCausalLM", "AutoModelForImageTextToText"):
        for key in ("dtype", "torch_dtype"):
            if model is not None or not hasattr(transformers, name):
                continue
            try:
                model = getattr(transformers, name).from_pretrained(**source, **{key: dtype})
                loader = name
            except Exception as exc:  # noqa: BLE001 - the next combination is the fallback
                errors.append(f"{name}({key}): {type(exc).__name__}: {str(exc)[:160]}")
    if model is None:
        raise SystemExit("[worker] could not load the model: " + " | ".join(errors))
    if spec.get("adapter"):
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, spec["adapter"])
    model = model.to("cuda" if cuda else "cpu").eval()
    load_seconds = time.time() - started
    print(f"[worker] loaded with {loader} in {load_seconds:.0f}s on {'cuda' if cuda else 'cpu'}", flush=True)
    template = spec.get("chat_template_kwargs") or {}

    def render(messages):
        try:
            return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, **template)
        except Exception:  # templates that only accept typed content parts
            parts = [{"role": m["role"], "content": [{"type": "text", "text": m["content"]}]} for m in messages]
            return tokenizer.apply_chat_template(parts, tokenize=False, add_generation_prompt=True, **template)

    texts = {p["id"]: render(p["messages"]) for p in todo}
    todo = sorted(todo, key=lambda p: -len(texts[p["id"]]))  # longest first: an out-of-memory batch shows up at once
    samples = int(spec.get("samples", 1))
    decode = {"max_new_tokens": int(spec.get("max_new_tokens", 768)), "pad_token_id": tokenizer.pad_token_id}
    if float(spec.get("temperature", 0)) > 0:
        decode.update(do_sample=True, temperature=float(spec["temperature"]), top_p=float(spec.get("top_p", 1.0)),
                      top_k=int(spec.get("top_k", 0)) or None, num_return_sequences=samples)
    else:
        decode.update(do_sample=False)
        samples = 1
    torch.manual_seed(int(spec.get("seed", 0)))
    ends = {tokenizer.eos_token_id, tokenizer.pad_token_id}
    batch_size = int(spec.get("batch_size") or (32 if cuda else 4))
    generated, cursor, generating = 0, 0, time.time()
    with out_path.open("a", encoding="utf-8") as sink:
        while cursor < len(todo):
            batch = todo[cursor:cursor + batch_size]
            try:
                enc = tokenizer([texts[p["id"]] for p in batch], return_tensors="pt", padding=True,
                                add_special_tokens=False).to(model.device)
                with torch.inference_mode():
                    result = model.generate(**enc, **decode)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if batch_size == 1:
                    raise
                batch_size = max(1, batch_size // 2)
                print(f"[worker] out of memory, batch size now {batch_size}", flush=True)
                continue
            new = result[:, enc["input_ids"].shape[1]:]
            for row, tokens in enumerate(new):
                ids = tokens.tolist()
                while ids and ids[-1] == tokenizer.pad_token_id:
                    ids.pop()
                finish = "stop" if (len(ids) < decode["max_new_tokens"] or (ids and ids[-1] in ends)) else "length"
                generated += len(ids)
                write(sink, batch[row // samples]["id"], row % samples, tokenizer.decode(ids, skip_special_tokens=True),
                      len(ids), finish)
            sink.flush()
            cursor += len(batch)
            rate = generated / max(1e-6, time.time() - generating)
            print(f"[worker] {cursor}/{len(todo)} prompts, {generated} tokens, {rate:.0f} tok/s", flush=True)
    return {"engine": "transformers " + transformers.__version__ + " " + loader, "torch": torch.__version__,
            "device": torch.cuda.get_device_name(0) if cuda else "cpu", "dtype": str(dtype), "samples": samples,
            "tokens_generated": generated, "load_seconds": round(load_seconds, 1),
            "generate_seconds": round(time.time() - generating, 1)}


def one(job, max_model_len):
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    prompts = [json.loads(line) for line in (job / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if line]
    out_path = job / "outputs.jsonl"
    done = set()
    if out_path.exists():
        done = {json.loads(line)["id"] for line in out_path.read_text(encoding="utf-8").splitlines() if line}
    todo = [p for p in prompts if p["id"] not in done]
    engine = spec.get("engine") or "hf"
    print(f"[worker] {spec['run_id']}: {len(todo)} of {len(prompts)} prompts to do, model {spec['model']}, engine {engine}",
          flush=True)
    if not todo:
        ran = {"engine": engine, "tokens_generated": 0}
    elif engine == "vllm":
        ran = run_vllm(spec, todo, out_path, max_model_len)
    else:
        ran = run_hf(spec, todo, out_path)
    meta = {"run_id": spec["run_id"], "model": spec["model"], "revision": spec.get("revision"), "tuned": spec.get("tuned"),
            "served_as": "adapter" if spec.get("lora") else "folded weights" if spec.get("weights") else "base",
            "python": sys.version.split()[0],
            "prompts": len(prompts), "answered_now": len(todo), "finished": time.time(), **ran}
    (job / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("[worker] done " + json.dumps({k: meta.get(k) for k in ("device", "engine", "tokens_generated", "generate_seconds")}),
          flush=True)


def main():
    """Every job directory given is answered in turn. Jobs that share a model share one loaded engine."""
    jobs = [Path(a) for a in sys.argv[1:]] or [Path(os.environ.get("RUU_JOB") or "job")]
    longest = max(int(json.loads((job / "spec.json").read_text(encoding="utf-8")).get("max_model_len", 8192)) for job in jobs)
    for job in jobs:
        one(job, longest)
    print("[worker] all done", flush=True)


if __name__ == "__main__":
    main()
