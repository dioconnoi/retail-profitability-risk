"""Smoke + sanity tests for the EDA step: figures are written and headline numbers are sane."""
import json

import pytest

from olist_risk import data, eda
from olist_risk.build_db import build


@pytest.fixture(scope="module")
def findings(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eda")
    db_path = build(tmp / "olist_eda.db")
    mp = pytest.MonkeyPatch()
    mp.setattr(eda, "query", lambda sql: data.query(sql, db_path))
    mp.setattr(eda, "FIG_DIR", tmp / "figures")
    mp.setattr(eda, "FINDINGS_PATH", tmp / "findings.json")
    try:
        result = eda.run()
    finally:
        mp.undo()
    return result, tmp


def test_all_figures_written(findings):
    _, tmp = findings
    pngs = sorted(p.name for p in (tmp / "figures").glob("*.png"))
    assert len(pngs) == 7 and pngs[0].startswith("01_") and pngs[-1].startswith("07_")
    assert all((tmp / "figures" / p).stat().st_size > 10_000 for p in pngs)


def test_findings_json_roundtrips(findings):
    result, tmp = findings
    on_disk = json.loads((tmp / "findings.json").read_text(encoding="utf-8"))
    assert on_disk["summary"]["orders"] == result["summary"]["orders"]


def test_headline_numbers_are_sane(findings):
    f, _ = findings
    assert 0.75 < f["sellers"]["top20pct_seller_share"] < 0.90
    assert f["lateness"]["lateness_multiplier"] > 3          # late orders review far worse
    assert f["drift"]["actual_days_jun_aug18"] < f["drift"]["actual_days_before_jun18"]
    assert f["revenue"]["last3_vs_spring"] < 0               # revenue slipped since spring 2018
    assert 0.15 < f["platform_p2_margin"] < 0.22
