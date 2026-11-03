"""Spend meter and hard cap for paid eval runs (D-047).

Integer micro-dollars throughout (like paise, no floats). `LLMClient(meter=...)` calls `check()`
before every API call and `record(usage)` after it; cache hits never reach either. A reply without
token usage stops the run, because an unmetered call could overrun the cap. The meter persists to a
JSON file after every call, so a resumed run continues from what was already spent.
"""

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intentlock.llm.client import LLMError

MICRO = 1_000_000
_USD = re.compile(r"\$?(\d{1,7})(?:\.(\d{1,6}))?")


class BudgetExceeded(LLMError):
    """The spend cap is reached (or usage is unknown). Never retried; the run stops cleanly."""


def parse_usd_micro(text: str) -> int:
    m = _USD.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not a dollar amount: {text!r}")
    whole, frac = m.group(1), (m.group(2) or "").ljust(6, "0")
    return int(whole) * MICRO + int(frac)


@dataclass
class SpendMeter:
    price_in_per_mtok: int  # micro-dollars per million prompt tokens
    price_out_per_mtok: int  # micro-dollars per million completion tokens
    cap: int  # micro-dollars
    path: Path | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    spent: int = 0
    calls: int = 0
    stopped: str | None = None

    def __post_init__(self) -> None:
        if self.path is not None and self.path.exists():
            saved = json.loads(self.path.read_text())
            self.tokens_in, self.tokens_out = saved["tokens_in"], saved["tokens_out"]
            self.spent, self.calls = saved["spent_micro_usd"], saved["calls"]
            self.stopped = saved.get("stopped")  # review LOW-3: a stop survives a resume

    @classmethod
    def from_env(cls, path: Path | None) -> "SpendMeter":
        values = {}
        for name in ("MODEL_PRICE_IN_PER_MTOK", "MODEL_PRICE_OUT_PER_MTOK", "EVAL_SPEND_CAP_USD"):
            raw = os.environ.get(name, "")
            if not raw.strip():
                raise ValueError(f"{name} is not set in .env (needed for a paid eval run)")
            values[name] = parse_usd_micro(raw)
        return cls(
            price_in_per_mtok=values["MODEL_PRICE_IN_PER_MTOK"],
            price_out_per_mtok=values["MODEL_PRICE_OUT_PER_MTOK"],
            cap=values["EVAL_SPEND_CAP_USD"],
            path=path,
        )

    def check(self) -> None:
        if self.stopped:
            raise BudgetExceeded(self.stopped)
        if self.spent >= self.cap:
            self.stopped = f"spend cap reached: {self.spent} of {self.cap} micro-USD"
            self._save()
            raise BudgetExceeded(self.stopped)

    def record(self, usage: Any) -> None:
        tin = getattr(usage, "prompt_tokens", None)
        tout = getattr(usage, "completion_tokens", None)
        if not isinstance(tin, int) or not isinstance(tout, int) or tin < 0 or tout < 0:
            self.calls += 1
            self.stopped = "the provider reported no token usage; cannot meter spend"
            self._save()
            raise BudgetExceeded(self.stopped)
        cost = math.ceil((tin * self.price_in_per_mtok + tout * self.price_out_per_mtok) / MICRO)
        self.tokens_in += tin
        self.tokens_out += tout
        self.spent += cost
        self.calls += 1
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "tokens_in": self.tokens_in,
                    "tokens_out": self.tokens_out,
                    "spent_micro_usd": self.spent,
                    "calls": self.calls,
                    "cap_micro_usd": self.cap,
                    "price_in_micro_usd_per_mtok": self.price_in_per_mtok,
                    "price_out_micro_usd_per_mtok": self.price_out_per_mtok,
                    "stopped": self.stopped,
                },
                indent=1,
            )
            + "\n"
        )
        tmp.replace(self.path)
