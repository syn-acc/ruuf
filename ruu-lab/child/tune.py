"""The child system's trainer: fit a LoRA adapter to replies the model itself wrote, then fold it in.

Runs on the GPU machine (any machine with torch, transformers and peft will do; a CPU is enough to
test it). It is given the conversations uk chose and nothing else: no test item, no seed, no score.

    SRC/tune.json       settings (written by keep.py on uk)
    SRC/train.jsonl     {"messages", "reply"} per line
    OUT/adapter/        the adapter alone: small, the thing worth keeping
    OUT/merged/         full weights with the adapter folded in, laid out like the base model. This is
                        what the child is served from by default: every change is in the weights, and
                        the third loss below says whether the fold kept them.
    OUT/tune_meta.json  what ran: versions, steps, loss by step, seconds, and the loss on one fixed
                        slice of the training data three ways (base model, with the adapter, folded
                        in). Folding into 16-bit weights can round an adapter away; if it did, the
                        third number says so here instead of as a mystery in the scores.

    tune.py SRC OUT

It prints a "[tune]" line as it goes; the launcher treats silence as a hang.
"""
import collections
import gc
import json
import math
import os
import random
import shutil
import sys
import time
from pathlib import Path

SKIP = ("vision", "visual", "audio", "lm_head", "embed", "merger", "projector")  # adapt the language model's layers only
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")  # less memory lost to fragmentation


def say(text):
    print("[tune] " + text, flush=True)


def load_base(spec, dtype):
    """The checkpoint's own class first, so that what is saved later is laid out like what was loaded."""
    import transformers

    source = {"pretrained_model_name_or_path": spec.get("base_weights") or spec["model"],
              "revision": None if spec.get("base_weights") else (spec.get("revision") or None)}
    errors = []
    for name in ("AutoModelForImageTextToText", "AutoModelForCausalLM"):
        for key in ("dtype", "torch_dtype"):
            if not hasattr(transformers, name):
                continue
            try:
                return getattr(transformers, name).from_pretrained(**source, **{key: dtype}), name, source
            except Exception as exc:  # noqa: BLE001 - the next combination is the fallback
                errors.append(f"{name}({key}): {type(exc).__name__}: {str(exc)[:160]}")
    raise SystemExit("[tune] could not load the model: " + " | ".join(errors))


def render(tokenizer, messages, template):
    """The prompt exactly as the answering engine builds it: training on a different prompt teaches a different model."""
    try:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, **template)
    except Exception:  # noqa: BLE001 - templates that only accept typed content parts
        parts = [{"role": m["role"], "content": [{"type": "text", "text": m["content"]}]} for m in messages]
        return tokenizer.apply_chat_template(parts, tokenize=False, add_generation_prompt=True, **template)


def encode(tokenizer, rows, template, max_tokens):
    """Token ids and labels per example. Only the reply is learned; the prompt is context."""
    examples, dropped = [], 0
    for row in rows:
        prompt = tokenizer(render(tokenizer, row["messages"], template), add_special_tokens=False)["input_ids"]
        reply = tokenizer(row["reply"] + tokenizer.eos_token, add_special_tokens=False)["input_ids"]
        if len(prompt) + len(reply) > max_tokens:
            dropped += 1
            continue
        examples.append((prompt + reply, [-100] * len(prompt) + reply))
    return examples, dropped


def plan_epoch(examples, batch, batch_tokens, rng):
    """Batches of similar length, capped by padded size, in random order."""
    order = sorted(range(len(examples)), key=lambda i: len(examples[i][0]))
    batches, current = [], []
    for index in order:
        longest = len(examples[index][0])  # sorted, so the newest is the longest
        if current and (len(current) + 1 > batch or (len(current) + 1) * longest > batch_tokens):
            batches.append(current)
            current = []
        current.append(index)
    if current:
        batches.append(current)
    rng.shuffle(batches)
    return batches


def collate(examples, indexes, pad, device):
    import torch

    width = max(len(examples[i][0]) for i in indexes)
    ids = torch.full((len(indexes), width), pad, dtype=torch.long)
    labels = torch.full((len(indexes), width), -100, dtype=torch.long)
    mask = torch.zeros((len(indexes), width), dtype=torch.long)
    for row, i in enumerate(indexes):
        tokens, target = examples[i]
        ids[row, :len(tokens)] = torch.tensor(tokens)
        labels[row, :len(target)] = torch.tensor(target)
        mask[row, :len(tokens)] = 1
    return ids.to(device), mask.to(device), labels.to(device)


def forward(model, batch, cuda):
    import torch

    ids, mask, labels = batch
    if cuda:
        with torch.autocast("cuda", dtype=torch.bfloat16):
            return model(input_ids=ids, attention_mask=mask, labels=labels).loss
    return model(input_ids=ids, attention_mask=mask, labels=labels).loss


def loss_on(model, examples, slices, pad, device, cuda):
    """Mean loss per reply token over a fixed set of batches."""
    import torch

    model.eval()
    total, tokens = 0.0, 0
    with torch.no_grad():
        for indexes in slices:
            count = sum(sum(t != -100 for t in examples[i][1]) for i in indexes)
            total += float(forward(model, collate(examples, indexes, pad, device), cuda)) * count
            tokens += count
    return round(total / max(1, tokens), 4)


def layout(merged, base_source):
    """Do the saved weights have the tensors the base checkpoint has? An engine that loads the base by
    name will want the same names."""
    try:
        from huggingface_hub import snapshot_download
        from safetensors import safe_open

        def names(folder):
            found = set()
            for path in Path(folder).glob("*.safetensors"):
                with safe_open(str(path), "pt") as handle:
                    found |= set(handle.keys())
            return found

        source = base_source["pretrained_model_name_or_path"]
        base = source if os.path.isdir(source) else snapshot_download(
            source, revision=base_source["revision"], allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt", "*.model"])
        for path in Path(base).iterdir():  # tokenizer, chat template, processor settings: anything small the save left out
            if (path.is_file() and path.suffix != ".safetensors" and not path.name.endswith(".index.json")
                    and not (Path(merged) / path.name).exists()):  # never the base's list of weight files: ours differ
                shutil.copy(path, Path(merged) / path.name)
        a, b = names(base), names(merged)
        report = {"base_tensors": len(a), "missing": len(a - b), "extra": len(b - a), "examples": sorted(a ^ b)[:4]}
        if a - b:
            # Tensors the checkpoint carries but this class does not load (seen 2026-11-20: 15 named mtp.*).
            # Training cannot have touched them, so copy them across unchanged for an engine that wants them.
            from safetensors.torch import save_file

            carried = {}
            for path in Path(base).glob("*.safetensors"):
                with safe_open(str(path), "pt") as handle:
                    carried.update({name: handle.get_tensor(name) for name in handle.keys() if name in a - b})
            save_file(carried, str(Path(merged) / "model-carried-over.safetensors"), metadata={"format": "pt"})
            index = Path(merged) / "model.safetensors.index.json"
            if index.exists():  # a sharded save lists its files; add ours to the list
                listed = json.loads(index.read_text(encoding="utf-8"))
                listed["weight_map"].update({name: "model-carried-over.safetensors" for name in carried})
                index.write_text(json.dumps(listed, indent=2), encoding="utf-8")
            report.update(carried_over=len(carried), missing=len(a - names(merged)))
        return report
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is reported, not fatal
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def main(src, out):
    import peft
    import torch
    import transformers
    from transformers import AutoTokenizer

    spec = json.loads((src / "tune.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (src / "train.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    cuda = torch.cuda.is_available()
    device, dtype = ("cuda", torch.bfloat16) if cuda else ("cpu", torch.float32)
    if not cuda and spec.get("threads"):
        torch.set_num_threads(int(spec["threads"]))
    seed = int(spec.get("seed", 0))
    torch.manual_seed(seed)
    rng = random.Random(seed)
    started = time.time()
    model, loader, source = load_base(spec, dtype)
    tokenizer = AutoTokenizer.from_pretrained(**source)
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    model = model.to(device)
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    say(f"loaded {spec['model']} with {loader} in {time.time() - started:.0f}s on {device}")

    examples, dropped = encode(tokenizer, rows, spec.get("chat_template_kwargs") or {}, int(spec.get("max_tokens", 2048)))
    if not examples:
        raise SystemExit("[tune] no example fits the token limit")
    batch, batch_tokens = int(spec.get("batch", 8)), int(spec.get("batch_tokens", 8192))
    order = list(range(len(examples)))
    random.Random(seed).shuffle(order)
    fixed = [examples[i] for i in order[:32]]  # the slice the three losses are measured on
    check = plan_epoch(fixed, batch, batch_tokens, random.Random(seed))
    loss = {"before": loss_on(model, fixed, check, pad, device, cuda)}
    say(f"{len(examples)} examples ({dropped} too long), loss before {loss['before']}")

    names = [name for name, module in model.named_modules()
             if isinstance(module, torch.nn.Linear) and not any(part in name.lower() for part in SKIP)]
    if not names:
        raise SystemExit("[tune] found no linear layer to adapt")
    if cuda and spec.get("checkpoint", True):
        # Without this one 1,900-token example did not fit in an L4's 22 GB (2026-11-20): the reference
        # kernel for this model's linear attention keeps a lot per layer for the backward pass. Checkpointing
        # keeps one layer's worth at a time and recomputes the rest, for some extra time.
        try:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
            model.enable_input_require_grads()
            say("gradient checkpointing on")
        except Exception as exc:  # noqa: BLE001
            say(f"gradient checkpointing not available: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        # A runtime can ship a torchao too old for peft, and peft then refuses to build any adapter at all
        # (Colab, 2026-11-20: torchao 0.10.0, peft wanted 0.16). Plain linear layers never need torchao.
        import peft.tuners.lora.torchao as lora_torchao
        lora_torchao.is_torchao_available = lambda: False
    except Exception:  # noqa: BLE001 - a peft without that module has no such check
        pass
    config = peft.LoraConfig(r=int(spec.get("rank", 16)), lora_alpha=int(spec.get("alpha", 32)),
                             lora_dropout=float(spec.get("dropout", 0.05)), target_modules=names, bias="none")
    model = peft.get_peft_model(model, config)
    params = [p for p in model.parameters() if p.requires_grad]
    trainable = sum(p.numel() for p in params)
    say(f"adapting {len(names)} layers, {trainable / 1e6:.1f}M trainable weights, e.g. {names[0]}")

    epochs = int(spec.get("epochs", 2))
    queue = collections.deque(b for _ in range(epochs) for b in plan_epoch(examples, batch, batch_tokens, rng))
    total, warm = len(queue), max(1, len(queue) // 20)
    optim = torch.optim.AdamW(params, lr=float(spec.get("lr", 1e-4)), weight_decay=0.0)

    def rate(step):  # short warm-up, then a cosine down to a tenth
        if step < warm:
            return (step + 1) / warm
        return 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, (step - warm) / max(1, total - warm))))

    schedule = torch.optim.lr_scheduler.LambdaLR(optim, rate)
    out_of_memory = getattr(torch, "OutOfMemoryError", torch.cuda.OutOfMemoryError)
    step, curve, recent, training = 0, [], collections.deque(maxlen=10), time.time()
    model.train()
    while queue:
        indexes, value, fits = queue.popleft(), None, True
        try:
            value = forward(model, collate(examples, indexes, pad, device), cuda)
            value.backward()
        except out_of_memory:
            fits = False
        if not fits:  # handled out here: inside the except block the failed pass is still held by the traceback
            value = None
            optim.zero_grad(set_to_none=True)
            gc.collect()
            torch.cuda.empty_cache()
            if len(indexes) == 1:
                raise SystemExit(f"[tune] one example of {len(examples[indexes[0]][0])} tokens does not fit in GPU memory")
            say(f"out of memory on a batch of {len(indexes)}, splitting it")
            queue.appendleft(indexes[len(indexes) // 2:])
            queue.appendleft(indexes[:len(indexes) // 2])
            continue
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optim.step()
        schedule.step()
        optim.zero_grad(set_to_none=True)
        step += 1
        recent.append(float(value))
        curve.append(round(float(value), 4))
        if step == 1 or step % max(1, total // 20) == 0 or not queue:
            peak = f", peak memory {torch.cuda.max_memory_allocated() / 2 ** 30:.1f} GB" if cuda else ""
            say(f"step {step}/{total} loss {sum(recent) / len(recent):.3f}, {time.time() - training:.0f}s{peak}")
    train_seconds = time.time() - training
    loss["adapter"] = loss_on(model, fixed, check, pad, device, cuda)

    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out / "adapter"))
    folding = time.time()
    meta = {"tune_id": spec.get("tune_id"), "model": spec["model"], "revision": spec.get("revision"), "loader": loader,
            "device": torch.cuda.get_device_name(0) if cuda else "cpu", "dtype": str(dtype), "torch": torch.__version__,
            "transformers": transformers.__version__, "peft": peft.__version__, "python": sys.version.split()[0],
            "examples": len(examples), "dropped_too_long": dropped, "layers_adapted": len(names), "layer_example": names[0],
            "trainable_weights": trainable, "steps": step, "loss_by_step": curve[::max(1, len(curve) // 60)],
            "train_seconds": round(train_seconds, 1), "loss": loss}
    if spec.get("fold", True):
        model = model.merge_and_unload()
        loss["merged"] = loss_on(model, fixed, check, pad, device, cuda)
        model.save_pretrained(str(out / "merged"), safe_serialization=True)
        tokenizer.save_pretrained(str(out / "merged"))
        meta["layout"] = layout(out / "merged", source)
        meta["fold_seconds"] = round(time.time() - folding, 1)
    say(f"loss on the fixed slice: before {loss['before']}, with adapter {loss['adapter']}, folded in {loss.get('merged')}")
    if meta.get("layout", {}).get("missing") or meta.get("layout", {}).get("error"):
        say("the folded weights are not laid out like the base: " + json.dumps(meta["layout"])[:300])
    meta["finished"] = time.time()
    (out / "tune_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    say("all done")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
