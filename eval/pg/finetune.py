"""Fine-tune Llama Prompt Guard 2 86M on commerce injections and compare it with the stock model
(SPEC §1 stretch #13, D-046). CPU only.

  python -m eval.pg.finetune lock               hash the data files (do this before training)
  python -m eval.pg.finetune train              -> .cache/pg-finetuned/
  python -m eval.pg.finetune eval               stock vs fine-tuned on every split
                                                -> eval/results/<ts>-pg/summary.json

The word embeddings (192M of the 278M parameters) are frozen: it fits in a few GB of RAM and limits
how far the model can drift from what it already knows. Scores are computed exactly as the gateway
does (probability of LABEL_1, per mille, flagged at >= 500), so the numbers carry over to step 9.
Training never reads the test, fixtures, jailbreaks or catalog files.
"""

import hashlib
import json
import random
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
LOCK = Path(__file__).resolve().parent / "DATA.lock"
ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / ".cache" / "pg-finetuned"
RESULTS = ROOT / "eval" / "results"
BASE = "meta-llama/Llama-Prompt-Guard-2-86M"
THRESHOLD = 500
SPLITS = ("test", "fixtures", "jailbreaks", "catalog")
HYPER = {"epochs": 3, "lr": 3e-5, "batch": 16, "max_len": 128, "seed": 0}


def rows(name: str, data_dir: Path = DATA) -> list[dict]:
    lines = (data_dir / f"{name}.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines]


def digests(data_dir: Path = DATA) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.jsonl"))
    }


def write_lock(data_dir: Path = DATA, lock: Path = LOCK) -> dict[str, str]:
    if lock.exists():
        raise FileExistsError(f"{lock} exists; the data is locked")
    d = digests(data_dir)
    lock.write_text(json.dumps(d, indent=1) + "\n")
    return d


def check_lock(data_dir: Path = DATA, lock: Path = LOCK) -> dict[str, str]:
    if not lock.exists():
        raise RuntimeError("no DATA.lock: run `python -m eval.pg.finetune lock` first")
    want, got = json.loads(lock.read_text()), digests(data_dir)
    if want != got:
        raise RuntimeError(
            f"data changed since it was locked: {sorted(set(want.items()) ^ set(got.items()))}"
        )
    return got


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def load(model: str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    return AutoTokenizer.from_pretrained(model), AutoModelForSequenceClassification.from_pretrained(
        model
    )


def scores(tok, model, texts: list[str], max_len: int = 512, batch: int = 32) -> list[int]:
    import torch

    dev = device()
    model.to(dev).eval()
    out: list[int] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            enc = tok(texts[i : i + batch], return_tensors="pt", padding=True, truncation=True,
                      max_length=max_len).to(dev)  # fmt: skip
            p = model(**enc).logits.softmax(-1)[:, 1]
            out += [min(max(round(float(x) * 1000), 0), 1000) for x in p]
    return out


def train(base: str, train_rows: list[dict], out_dir: Path, **hyper) -> dict:
    import torch

    h = {**HYPER, **hyper}
    random.seed(h["seed"])
    torch.manual_seed(h["seed"])
    dev = device()
    tok, model = load(base)
    model.to(dev)
    model.base_model.embeddings.word_embeddings.weight.requires_grad_(False)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=h["lr"])
    data = list(train_rows)
    started, losses = time.time(), []
    model.train()
    for _ in range(h["epochs"]):
        random.shuffle(data)
        for i in range(0, len(data), h["batch"]):
            chunk = data[i : i + h["batch"]]
            enc = tok([r["text"] for r in chunk], return_tensors="pt", padding=True,
                      truncation=True, max_length=h["max_len"]).to(dev)  # fmt: skip
            labels = torch.tensor([r["label"] for r in chunk], device=dev)
            loss = model(**enc, labels=labels).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            losses.append(float(loss.detach()))
    out_dir.mkdir(parents=True, exist_ok=True)
    model.to("cpu").save_pretrained(out_dir)  # CPU tensors: loadable by the gateway anywhere
    tok.save_pretrained(out_dir)
    info = {
        "base": base,
        "device": dev,
        "hyper": h,
        "trainable_params": sum(p.numel() for p in params),
        "steps": len(losses),
        "final_loss": round(sum(losses[-10:]) / max(len(losses[-10:]), 1), 4),
        "seconds": round(time.time() - started, 1),
    }
    (out_dir / "training.json").write_text(json.dumps(info, indent=1) + "\n")
    return info


def metrics(rows_: list[dict], s: list[int], by: str | None = None) -> dict:
    def count(items):
        flagged = [(r["label"], sc >= THRESHOLD) for r, sc in items]
        pos = [f for lab, f in flagged if lab == 1]
        neg = [f for lab, f in flagged if lab == 0]
        return {
            "injections": len(pos),
            "detected": sum(pos),
            "benign": len(neg),
            "false_positives": sum(neg),
        }

    out = count(list(zip(rows_, s, strict=True)))
    if by:
        groups: dict[str, list] = {}
        for r, sc in zip(rows_, s, strict=True):
            groups.setdefault(str(r.get(by) or r.get("family") or "-"), []).append((r, sc))
        out["by_" + by] = {k: count(v) for k, v in sorted(groups.items())}
    return out


def evaluate(models: dict[str, str], data_dir: Path = DATA) -> dict:
    loaded = {name: load(path) for name, path in models.items()}
    out: dict = {}
    for split in SPLITS:
        rs = rows(split, data_dir)
        by = {"test": "style", "fixtures": "fixture"}.get(split)
        out[split] = {
            name: metrics(rs, scores(tok, model, [r["text"] for r in rs]), by)
            for name, (tok, model) in loaded.items()
        }
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "lock":
        print(json.dumps(write_lock(), indent=1))
        return 0
    if cmd == "train":
        check_lock()
        print(json.dumps(train(BASE, rows("train"), OUT), indent=1))
        return 0
    if cmd == "eval":
        data = check_lock()
        started = time.time()
        summary = {
            "data_sha256": data,
            "threshold_permille": THRESHOLD,
            "training": json.loads((OUT / "training.json").read_text()),
            "splits": evaluate({"stock": BASE, "finetuned": str(OUT)}),
        }
        summary["seconds"] = round(time.time() - started, 1)
        folder = RESULTS / (time.strftime("%Y%m%d-%H%M%S") + "-pg")
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        print(json.dumps(summary["splits"], indent=1))
        print(f"-> {folder}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
