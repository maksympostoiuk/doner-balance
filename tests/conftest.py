import copy
from pathlib import Path

import pytest
import yaml

from doner_balance.config import EconomyConfig, config_from_dict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "economy.yaml"


def tiny_dict() -> dict:
    """A tiny, hand-computable economy (2 tiers) built on top of the default config's meta sections."""
    base = yaml.safe_load(DEFAULT_CONFIG.read_text())
    d = copy.deepcopy(base)
    d["staff_milestones"] = [{"level": 10, "mult": 2}]
    d["tap"] = {
        "base_value": 1,
        "upgrades": [
            {"id": "knife", "name": "Knife", "kind": "flat", "value": 1, "base_cost": 10, "cost_growth": 1.5},
            {"id": "wrap", "name": "Wrap", "kind": "pct", "value": 0.5, "base_cost": 100, "cost_growth": 2},
            {"id": "show", "name": "Show", "kind": "income_share", "value": 0.1, "base_cost": 1000, "cost_growth": 3},
        ],
    }
    d["tiers"] = [
        {
            "id": "kiosk",
            "name": "Kiosk",
            "unlock_cost": 0,
            "tap_mult": 1,
            "global_income_mult": 1,
            "staff": [{"id": "helper", "name": "Helper", "base_cost": 10, "cost_growth": 1.1, "income": 1}],
            "upgrades": [
                {"id": "sauce", "name": "Sauce", "kind": "income", "value": 0.1, "base_cost": 50, "cost_growth": 2},
                {"id": "grill", "name": "Grill", "kind": "speed", "value": 0.5, "base_cost": 80, "cost_growth": 2},
            ],
            "managers": [{"id": "boss", "name": "Boss", "cost": 100, "income_mult": 2, "offline_cap_hours_add": 1}],
        },
        {
            "id": "cafe",
            "name": "Cafe",
            "unlock_cost": 1000,
            "tap_mult": 3,
            "global_income_mult": 1.5,
            "free_staff_levels": {"waiter": 1},
            "staff": [{"id": "waiter", "name": "Waiter", "base_cost": 500, "cost_growth": 1.1, "income": 20}],
            "upgrades": [],
            "managers": [{"id": "cafe_boss", "name": "Cafe boss", "cost": 2000}],
        },
    ]
    d["prestige"].update(
        {
            "required_tier": "cafe",
            "base_requirement": 1000,
            "exponent": 0.5,
            "scale": 1,
            "bonus_per_point": 0.1,
            "min_points": 1,
            "perks": [],  # keep the tiny economy hand-computable; perks have their own tests
        }
    )
    d["offline"] = {"efficiency": 0.5, "cap_hours": 2, "requires_manager": True}
    for e in d["events"]:
        e["min_tier"] = "kiosk"
    d["pacing"]["targets"] = [
        {"milestone": "cafe", "hours": 0.1, "clock": "active", "lever": "tier:cafe"},
    ]
    d["simulation"]["days"] = 1
    d["simulation"]["extra_seeds"] = [1]
    return d


@pytest.fixture
def tiny() -> EconomyConfig:
    return config_from_dict(tiny_dict())


@pytest.fixture
def tiny_data() -> dict:
    return tiny_dict()


@pytest.fixture(scope="session")
def default_cfg() -> EconomyConfig:
    from doner_balance.config import load_config

    return load_config(DEFAULT_CONFIG)
