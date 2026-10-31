"""Expected outcomes are fixed before any run (SPEC §13.1; D-038).

`eval/cases/EXPECTED.lock` holds a sha256 per case of the whole case except `notes` (inputs and
expectations alike), so neither can be changed to fit a result. `python -m eval.lock` adds entries
for new case IDs only and refuses to change an entry that exists. `run_g` refuses to run if any
case is unlocked or changed, or if a locked case is missing; every result records the sha256 of
the lock and of each case file.
"""

import hashlib
import json
import sys
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "cases"
LOCK = "EXPECTED.lock"
UNLOCKED_FIELDS = ("notes",)  # free-text explanation only


class LockError(RuntimeError):
    pass


def load_cases(cases_dir: Path = CASES_DIR) -> list[dict]:
    cases = []
    for path in sorted(cases_dir.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    cases.append(json.loads(line))
                except ValueError:
                    raise LockError(f"{path.name}:{n} is not valid JSON") from None
    ids = [c["id"] for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise LockError(f"duplicate case IDs: {dupes}")
    return cases


def digest(case: dict) -> str:
    locked = {k: v for k, v in case.items() if k not in UNLOCKED_FIELDS}
    return hashlib.sha256(json.dumps(locked, sort_keys=True).encode()).hexdigest()


def read_lock(cases_dir: Path = CASES_DIR) -> dict[str, str]:
    path = cases_dir / LOCK
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def verify(cases: list[dict], cases_dir: Path = CASES_DIR) -> None:
    lock = read_lock(cases_dir)
    unlocked = [c["id"] for c in cases if c["id"] not in lock]
    changed = [c["id"] for c in cases if c["id"] in lock and lock[c["id"]] != digest(c)]
    missing = sorted(set(lock) - {c["id"] for c in cases})
    if unlocked or changed or missing:
        raise LockError(
            f"not locked: {unlocked}; changed after locking: {changed}; missing: {missing}"
        )


def fingerprints(cases_dir: Path = CASES_DIR) -> dict:
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    return {
        "lock_sha256": sha(cases_dir / LOCK),
        "case_files_sha256": {p.name: sha(p) for p in sorted(cases_dir.glob("*.jsonl"))},
    }


def add_new(cases_dir: Path = CASES_DIR) -> list[str]:
    cases, lock = load_cases(cases_dir), read_lock(cases_dir)
    changed = [c["id"] for c in cases if c["id"] in lock and lock[c["id"]] != digest(c)]
    if changed:
        raise LockError(f"refusing to re-lock changed cases: {changed}")
    added = [c["id"] for c in cases if c["id"] not in lock]
    lock.update({c["id"]: digest(c) for c in cases if c["id"] in added})
    (cases_dir / LOCK).write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    return added


if __name__ == "__main__":
    try:
        print(f"locked {len(add_new())} new cases")
    except LockError as exc:
        print(f"error: {exc}")
        sys.exit(1)
