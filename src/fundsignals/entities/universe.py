"""Fund universe model and CSV loader."""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Fund(BaseModel):
    """One fund in the target universe."""

    model_config = ConfigDict(frozen=True)

    fund_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    website: str = ""
    hq: str = ""


def load_universe(path: str | Path) -> list[Fund]:
    """Read fund_id,name,aliases(pipe-separated),website,hq rows from a CSV file."""
    funds: list[Fund] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            fund_id = (row.get("fund_id") or "").strip()
            name = (row.get("name") or "").strip()
            if not fund_id or not name:
                continue
            if fund_id in seen:
                raise ValueError(f"duplicate fund_id in universe: {fund_id}")
            seen.add(fund_id)
            aliases = [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
            funds.append(
                Fund(
                    fund_id=fund_id,
                    name=name,
                    aliases=aliases,
                    website=(row.get("website") or "").strip(),
                    hq=(row.get("hq") or "").strip(),
                )
            )
    return funds
