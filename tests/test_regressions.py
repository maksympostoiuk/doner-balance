"""Regression tests for bugs found in the second-pass code review."""

import copy
import itertools
import math

import pandas as pd
import pytest
import yaml

from doner_balance import formulas as F
from doner_balance.analysis import event_impact_runs, event_impact_table, milestone_table, run_table
from doner_balance.config import config_from_dict
from doner_balance.events import EventEngine
from doner_balance.model import Economy, Modifier, modifier_key
from doner_balance.recommend import casual_rules, prestige_rules
from doner_balance.simulator import milestone_names, simulate
from doner_balance.strategies import make_strategy

from .conftest import DEFAULT_CONFIG


def _default_dict() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG.read_text())


def _renamed_dict() -> dict:
    """Default economy with the last tier renamed: nothing may assume the name 'franchise'."""
    d = _default_dict()
    d["tiers"][-1]["id"] = "chain"
    d["prestige"]["required_tier"] = "chain"
    for t in d["pacing"]["targets"]:
        if t["milestone"] == "franchise":
            t["milestone"], t["lever"] = "chain", "tier:chain"
    return d


def test_smart_player_does_not_pay_for_maxed_permanent_bonus(default_cfg):
    econ = Economy(default_cfg)
    smart = make_strategy(econ, "best_roi")
    ev = next(e for e in default_cfg.events if e.id == "inspection")
    renovate = ev.response("renovate")
    st = econ.new_state()
    st.cash = 1e6
    st.levels.update(nephew=40, grill_cook=30)
    inc = sum(econ.active_income(st, smart.profile))
    rate = smart.effective_rate(st)
    worth_it = smart.expected_cost(renovate, st, inc, rate, ev)
    key = modifier_key(ev.id, renovate.id, 0)
    st.modifiers = [Modifier(ev.id, key, 1.03, None) for _ in range(renovate.effects[0].max_stacks)]
    maxed = smart.expected_cost(renovate, st, inc, rate, ev)
    assert maxed == pytest.approx(F.amount_value(renovate.cost, st.cash, inc))  # only the cost is left
    assert maxed > worth_it
    aff = EventEngine(econ, 1).affordable(ev, st, inc)
    assert smart.choose_response(ev, st, aff, inc).id != "renovate"


def test_milestones_follow_config_tier_names():
    cfg = config_from_dict(_renamed_dict())
    assert milestone_names(cfg) == ["first_upgrade", "first_manager", "cafe", "restaurant", "chain", "first_prestige"]
    r = simulate(cfg, "best_roi", days=8)
    assert "chain" in r.milestones
    assert "chain" in set(milestone_table(cfg, {"best_roi": r})["milestone"])


def test_event_impact_uses_last_tier_by_default():
    d = _renamed_dict()
    d["simulation"].update(days=8, extra_seeds=[])
    d["events"] = d["events"][:1]
    cfg = config_from_dict(d)
    runs = event_impact_runs(cfg, ["best_roi"], jobs=1)
    imp = event_impact_table(cfg, runs)
    assert set(imp["milestone"]) == {"chain"}
    assert imp["milestone_day_with"].notna().all()


def test_trailing_offline_time_fires_no_events(default_cfg):
    cfg = default_cfg.model_copy(deep=True)
    for e in cfg.events:
        e.rate_per_hour, e.cooldown_hours, e.min_hours_since_start = 5.0, 0.0, 0.0
        e.min_tier = "kiosk"
    r = simulate(cfg, "best_roi", days=1.5)
    fired = sum(s.fired for s in r.event_stats.values())
    assert fired == len(r.events)  # every fired event was answered in a session
    last_session_end = r.sessions["end_h"].iloc[-1]
    assert (r.events["t_h"] <= last_session_end + 1e-9).all()


def test_reputation_gained_is_numeric(default_cfg):
    cfg = default_cfg.model_copy(deep=True)
    cfg.prestige.currency_name = "street cred points"  # spaces must not break parsing
    r = simulate(cfg, "saver", days=21)
    runs = run_table(cfg, r)
    prest = r.purchases[r.purchases["kind"] == "prestige"]
    assert len(prest) >= 1
    assert runs["reputation_gained"].iloc[0] == prest["level"].iloc[0]
    assert runs["reputation_gained"].sum() == r.final["reputation"]


def test_prestige_rule_reads_the_reference_strategy():
    cfg = config_from_dict(_default_dict())
    ref = simulate(cfg, "best_roi", days=21)
    runs = pd.DataFrame(
        [
            {"strategy": "other", "run": 1, "franchise_after_h": 1000.0, "reputation_gained": 999},
            {"strategy": "other", "run": 2, "franchise_after_h": 999.0, "reputation_gained": 0},
            {"strategy": "best_roi", "run": 1, "franchise_after_h": 150.0, "reputation_gained": 54},
            {"strategy": "best_roi", "run": 2, "franchise_after_h": 50.0, "reputation_gained": 0},
        ]
    )
    recs = prestige_rules(cfg, ref, runs)
    loop = [r for r in recs if r.area == "prestige" and "Run 2" in r.evidence]
    assert loop and "x3.0" in loop[0].evidence and "54 reputation" in loop[0].evidence


def test_casual_rule_survives_reference_that_never_progresses():
    d = _default_dict()
    for t in d["tiers"][1:]:
        t["unlock_cost"] = 1e30
    cfg = config_from_dict(d)
    results = {n: simulate(cfg, n, days=1) for n in ("best_roi", "casual")}
    assert casual_rules(cfg, results) == []


def test_cost_formulas_do_not_overflow():
    assert F.level_cost(10, 1.15, 100_000) == math.inf
    assert F.bulk_cost(10, 1.15, 100_000, 10) == math.inf
    assert F.max_affordable(10, 1.15, 100_000, 1e300) == 0


def test_replan_interval_comes_from_config(default_cfg, monkeypatch):
    """The simulator must read the re-planning interval from the config, not a constant."""
    from doner_balance import simulator as sim_mod

    cfg = default_cfg.model_copy(deep=True)
    cfg.simulation.replan_every_seconds = 7.0
    sim = sim_mod.Simulator(cfg, "best_roi", days=0.01)
    calls: list[float] = []
    real_choose = sim.strategy.choose

    def spy(st):
        calls.append(sim.t)
        return real_choose(st)

    monkeypatch.setattr(sim.strategy, "choose", spy)
    sim.run()
    gaps = [b - a for a, b in itertools.pairwise(calls)]
    assert min(gaps) <= 7.0 and max(gaps) <= 7.0 + 1e-9


# --------------------------------------------------------------------------- validation & security


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["strategies"].update({"../../etc/x": d["strategies"]["best_roi"]}), "should match pattern"),
        (lambda d: d["tiers"][0]["staff"][0].update(id="nephew.x"), "should match pattern"),
        (lambda d: d["tiers"][0].update(name=""), "at least 1 character"),
        (lambda d: d["events"].append(copy.deepcopy(d["events"][0])), "event ids must be unique"),
        (
            lambda d: d["events"][0]["responses"].append(copy.deepcopy(d["events"][0]["responses"][0])),
            "response ids must be unique",
        ),
        (lambda d: d["profiles"]["regular"].update(sessions_per_day=100, session_minutes=10), "sessions overlap"),
        (lambda d: d["profiles"]["regular"].update(first_session_minutes=400), "sessions overlap"),
        (lambda d: d["tiers"][1].update(free_staff_levels={"waiter": 0}), "greater than or equal to 1"),
        (
            lambda d: (
                d["tiers"][1]["staff"][0].update(max_level=1) or d["tiers"][1].update(free_staff_levels={"waiter": 3})
            ),
            "exceeds max_level",
        ),
        (lambda d: d["tiers"][1].update(staff=[]), "at least 1 item"),
        (
            lambda d: d["events"][0]["responses"][0]["effects"].append({"cash_loss": {"flat": 1}, "duration_hours": 2}),
            "duration_hours only makes sense",
        ),
        (lambda d: d["analysis"].update(tap_share_low_pct=70, tap_share_high_pct=60), "tap_share_low_pct"),
        (lambda d: d["simulation"].update(extra_seeds=[42]), "must all be different"),
        (lambda d: d["simulation"].update(days=0), "greater than 0"),
        (lambda d: d["pacing"]["targets"][0].update(lever="tier:moon"), "unknown tier"),
        (lambda d: d["pacing"]["targets"][0].update(lever="tap.upgrades.ghost.base_cost"), "matches nothing"),
        (lambda d: d["pacing"]["targets"][0].update(lever="offline.requires_manager"), "is not a number"),
    ],
)
def test_new_validation_rules(mutate, message):
    from pydantic import ValidationError

    d = _default_dict()
    mutate(d)
    with pytest.raises(ValidationError, match=message):
        config_from_dict(d)


def test_dotted_path_helpers_edge_cases():
    from doner_balance.config import expand_path, get_path, set_path

    d = _default_dict()
    assert expand_path(d, "profiles.*.session_minutes") == [
        "profiles.regular.session_minutes",
        "profiles.casual.session_minutes",
    ]
    set_path(d, "staff_milestones.0", {"level": 30, "mult": 2})
    assert d["staff_milestones"][0] == {"level": 30, "mult": 2}
    with pytest.raises(KeyError):
        set_path(d, "tiers.moon.unlock_cost", 1)
    with pytest.raises(KeyError):
        get_path(d, "tiers.cafe.staff.ghost.income")


def test_scale_lever_keeps_ints_and_ignores_bools():
    from doner_balance.tuning import scale_lever

    d = _default_dict()
    new = scale_lever(d, ["tap.upgrades.knife.max_level", "offline.requires_manager", "tiers.cafe.unlock_cost"], 0.5)
    assert new["tap"]["upgrades"][0]["max_level"] == 25 and isinstance(new["tap"]["upgrades"][0]["max_level"], int)
    assert new["offline"]["requires_manager"] is True
    assert new["tiers"][1]["unlock_cost"] == pytest.approx(15000)


@pytest.mark.parametrize("v", [0, 3, 0.25, 4.3e7, 1e13, 2.5e-5, 1e-7, -3.2e8, 123456.5])
def test_format_yaml_number_round_trips(v):
    from doner_balance.tuning import format_yaml_number

    loaded = yaml.safe_load(format_yaml_number(v))
    assert isinstance(loaded, (int, float))
    assert loaded == pytest.approx(v, rel=5e-3)


def test_format_yaml_number_rejects_inf():
    from doner_balance.tuning import format_yaml_number

    with pytest.raises(ValueError):
        format_yaml_number(math.inf)


def _run_cli(args, capsys):
    from doner_balance.cli import main

    rc = main(args)
    return rc, capsys.readouterr()


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    rc, out = _run_cli(["--config", str(tmp_path / "missing.yaml"), "validate"], capsys)
    assert rc == 2 and "not found" in out.err
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    rc, out = _run_cli(["--config", str(empty), "validate"], capsys)
    assert rc == 2 and "is empty" in out.err
    broken = tmp_path / "broken.yaml"
    broken.write_text("tiers: [unclosed")
    rc, out = _run_cli(["--config", str(broken), "validate"], capsys)
    assert rc == 2 and "not valid YAML" in out.err
    invalid = tmp_path / "invalid.yaml"
    d = _default_dict()
    d["offline"]["efficiency"] = 5
    invalid.write_text(yaml.safe_dump(d))
    rc, out = _run_cli(["--config", str(invalid), "validate"], capsys)
    assert rc == 2 and "invalid economy config" in out.err and "efficiency" in out.err
    bad_ov = tmp_path / "ov.yaml"
    bad_ov.write_text("tiers.moon.unlock_cost: 5\n")
    rc, out = _run_cli(["--overrides", str(bad_ov), "validate"], capsys)
    assert rc == 2 and "bad override" in out.err


def test_cli_argument_checks(capsys):
    rc, out = _run_cli(["simulate", "best_roi", "--days", "0.05", "--disable", "ghost"], capsys)
    assert rc == 2 and "unknown NPC event" in out.err
    rc, out = _run_cli(["simulate", "nobody"], capsys)
    assert rc == 2 and "unknown strategy" in out.err
    rc, out = _run_cli(["tune", "--write-config"], capsys)
    assert rc == 2 and "--apply" in out.err
    with pytest.raises(SystemExit):
        _run_cli(["simulate", "--days", "-1"], capsys)


def test_cli_write_config_refuses_overrides(tmp_path, capsys):
    ov = tmp_path / "ov.yaml"
    ov.write_text("tiers.cafe.unlock_cost: 40000\n")
    rc, out = _run_cli(["--overrides", str(ov), "tune", "--apply", str(tmp_path / "o.yaml"), "--write-config"], capsys)
    assert rc == 2 and "cannot be combined" in out.err
