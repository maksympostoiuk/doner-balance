import json

import pytest

from doner_balance import formulas as F
from doner_balance.cli import main
from doner_balance.export import FORMAT, FORMAT_VERSION, build_export, export_json
from doner_balance.model import Economy
from doner_balance.simulator import simulate
from doner_balance.tuning import active_hours_at, check_targets, lever_paths, scale_lever


def test_export_roundtrip(default_cfg, tmp_path):
    p = export_json(default_cfg, tmp_path / "e.json")
    d = json.loads(p.read_text())
    assert d["format"] == FORMAT and d["format_version"] == FORMAT_VERSION
    assert [t["id"] for t in d["tiers"]] == [t.id for t in default_cfg.tiers]
    assert {e["id"] for e in d["events"]} == {e.id for e in default_cfg.events if e.enabled}
    # deterministic output
    assert build_export(default_cfg) == build_export(default_cfg)


def test_export_test_vectors_match_formulas(default_cfg):
    d = build_export(default_cfg)
    items = {u["id"]: u for u in d["tap"]["upgrades"]}
    for t in d["tiers"]:
        items.update({x["id"]: x for x in t["staff"] + t["upgrades"]})
    for v in d["test_vectors"]["level_cost"]:
        it = items[v["item"]]
        assert v["cost"] == pytest.approx(it["base_cost"] * it["cost_growth"] ** v["level"])
    s = d["test_vectors"]["sample_state"]
    econ = Economy(default_cfg)
    st = econ.new_state()
    st.tiers_owned, st.reputation = s["tiers_owned"], s["reputation"]
    st.levels.update(s["levels"])
    st.managers.update(s["managers"])
    assert econ.auto_income(st) == pytest.approx(s["expected"]["auto_income"])
    for v in d["test_vectors"]["reputation"]:
        p = d["prestige"]
        assert v["reputation_total"] == F.reputation_total(v["all_time_earnings"], p["base_requirement"],
                                                           p["exponent"], p["scale"])


def test_tier_lever_scales_whole_tier(tiny_data):
    paths = lever_paths(tiny_data, "tier:cafe")
    assert "tiers.cafe.unlock_cost" in paths and "tiers.cafe.staff.waiter.income" in paths
    assert "tiers.cafe.managers.cafe_boss.cost" in paths
    new = scale_lever(tiny_data, "tier:cafe", 2)
    assert new["tiers"][1]["unlock_cost"] == 2000
    assert new["tiers"][1]["staff"][0]["income"] == 40
    assert tiny_data["tiers"][1]["unlock_cost"] == 1000  # original untouched


def test_active_hours_at(default_cfg):
    # regular: sessions of 10 min at 0h, 5h, 10h, 24h ...
    assert active_hours_at(default_cfg, "best_roi", 24) == pytest.approx(0.5)
    assert active_hours_at(default_cfg, "best_roi", 5.05) == pytest.approx(1 / 6 + 0.05)


def test_check_targets_status(default_cfg):
    r = simulate(default_cfg, "best_roi", stop_when="restaurant")
    checks = {c.milestone: c for c in check_targets(default_cfg, r)}
    assert checks["first_upgrade"].status == "ok"
    assert checks["franchise"].status == "not reached"


def test_cli_validate_and_simulate(capsys):
    assert main(["validate"]) == 0
    assert "OK" in capsys.readouterr().out
    assert main(["simulate", "casual", "--days", "0.5"]) == 0
    assert "first_upgrade" in capsys.readouterr().out
