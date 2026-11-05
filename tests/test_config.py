import copy

import pytest
from pydantic import ValidationError

from doner_balance.config import config_from_dict, expand_path, get_path, set_path

from .conftest import tiny_dict


def test_default_config_is_valid(default_cfg):
    assert [t.id for t in default_cfg.tiers] == ["kiosk", "cafe", "restaurant", "franchise"]
    assert {e.npc for e in default_cfg.events} == {"supplier", "inspector", "mafia", "rival"}
    assert {"best_roi", "cheapest", "saver", "casual"} <= set(default_cfg.strategies)
    # cost growth inside the usual idle-game range
    for t in default_cfg.tiers:
        for s in t.staff:
            assert 1.07 <= s.cost_growth <= 1.15


def test_milestones_are_sorted():
    d = tiny_dict()
    d["staff_milestones"] = [{"level": 50, "mult": 2}, {"level": 10, "mult": 3}]
    cfg = config_from_dict(d)
    assert [m.level for m in cfg.staff_milestones] == [10, 50]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["tiers"][0].update(unlock_cost=5), "first tier must be free"),
    (lambda d: d["tiers"][1]["staff"].append(copy.deepcopy(d["tiers"][0]["staff"][0])), "globally unique"),
    (lambda d: d["tiers"][0]["staff"][0].update(cost_growth=1.0), "greater than 1"),
    (lambda d: d["events"][0].update(default_response="nope"), "default_response"),
    (lambda d: d["strategies"]["best_roi"].update(profile="ghost"), "unknown profile"),
    (lambda d: d["tiers"][1].update(free_staff_levels={"ghost": 1}), "unknown staff"),
    (lambda d: d["prestige"].update(required_tier="moon"), "required_tier"),
    (lambda d: d["tiers"][0].update(surprise=1), "Extra inputs"),
    (lambda d: d["pacing"]["targets"].append({"milestone": "moon", "hours": 1, "lever": "x"}), "target milestone"),
])
def test_invalid_configs_fail_loudly(mutate, message):
    d = tiny_dict()
    mutate(d)
    with pytest.raises(ValidationError, match=message):
        config_from_dict(d)


def test_dotted_paths():
    d = tiny_dict()
    assert get_path(d, "tiers.cafe.unlock_cost") == 1000
    assert get_path(d, "tap.upgrades.knife.base_cost") == 10
    set_path(d, "tiers.kiosk.staff.helper.income", 7)
    assert d["tiers"][0]["staff"][0]["income"] == 7
    assert expand_path(d, "tiers.*.unlock_cost") == ["tiers.kiosk.unlock_cost", "tiers.cafe.unlock_cost"]
    assert expand_path(d, "tiers.cafe.upgrades.*.base_cost") == []
