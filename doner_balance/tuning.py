"""Tuning helper: how far is the config from the pacing targets, and what to change.

How it works
------------
1. Run the reference strategy and read the time of every target milestone.
2. Calendar-clock targets are converted to the equivalent amount of *play time* using
   the session schedule (calendar time jumps from session to session, play time is
   continuous, so sensitivities measured in play time are stable).
3. For every off-target milestone the helper re-runs the simulation with the target's
   lever scaled (e.g. the cafe price x0.7), measures the elasticity
   ``e = d ln(time) / d ln(lever)`` and suggests ``factor = (target / actual) ** (1 / e)``.
4. ``auto_tune`` repeats this sequentially (earlier milestones first, because they shift
   everything after them) with a secant search in log-log space, and returns overrides.

Levers: ``tier:<id>`` scales every money value of a tier (unlock cost, staff costs and
incomes, upgrade and manager costs), which keeps the tier's internal paybacks intact.
Otherwise a dotted path (or list of paths) into the config; ``*`` matches all list items.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from .config import EconomyConfig, Target, config_from_dict, expand_path, get_path, set_path
from .sessions import session_schedule
from .simulator import SimResult, simulate

TIER_LEVER_FIELDS = (
    "unlock_cost",
    "staff.*.base_cost",
    "staff.*.income",
    "upgrades.*.base_cost",
    "managers.*.cost",
)


def lever_paths(data: dict[str, Any], lever: str | list[str]) -> list[str]:
    levers = [lever] if isinstance(lever, str) else list(lever)
    out: list[str] = []
    for lv in levers:
        if lv.startswith("tier:"):
            tid = lv.split(":", 1)[1]
            for f in TIER_LEVER_FIELDS:
                out += expand_path(data, f"tiers.{tid}.{f}")
        else:
            out += expand_path(data, lv)
    return out


def scale_lever(data: dict[str, Any], lever: str | list[str], factor: float) -> dict[str, Any]:
    """Return a copy of ``data`` with every lever value multiplied by ``factor``."""
    new = copy.deepcopy(data)
    for p in lever_paths(new, lever):
        v = get_path(new, p)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not v:
            continue
        if isinstance(v, int):  # e.g. max_level: stay an integer, at least 1
            set_path(new, p, max(1, round(v * factor)))
        else:
            set_path(new, p, float(v) * factor)
    return new


def lever_value(data: dict[str, Any], lever: str | list[str]) -> float:
    paths = lever_paths(data, lever)
    for p in paths:
        v = get_path(data, p)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            return float(v)
    return float("nan")


def nice(x: float, digits: int = 2) -> float:
    """Round to ``digits`` significant digits (for human-friendly suggestions)."""
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, -math.floor(math.log10(abs(x))) + digits - 1)


def active_hours_at(cfg: EconomyConfig, strategy: str, calendar_h: float) -> float:
    """Play time accumulated by ``calendar_h`` hours after install for a strategy's profile."""
    prof = cfg.profiles[cfg.strategies[strategy].profile]
    t = calendar_h * 3600.0
    total = 0.0
    for start, end in session_schedule(prof, calendar_h / 24.0 + 1):
        if start >= t:
            break
        total += min(end, t) - start
    return total / 3600.0


@dataclass
class TargetCheck:
    milestone: str
    description: str
    clock: str
    mode: str
    tolerance: float
    target_h: float
    actual_h: float | None
    target_active_h: float
    actual_active_h: float | None
    lever: str
    lever_value: float
    status: str = ""
    elasticity: float | None = None
    suggested_factor: float | None = None
    note: str = ""

    @property
    def ratio(self) -> float | None:
        """actual / target on the play-time clock (what the status is judged on)."""
        if self.actual_active_h is None:
            return None
        return self.actual_active_h / self.target_active_h

    @property
    def suggested_value(self) -> float | None:
        if self.suggested_factor is None or not math.isfinite(self.lever_value):
            return None
        return nice(self.lever_value * self.suggested_factor)


def _status(t: Target, actual_active: float | None, target_active: float) -> str:
    """Judged on the play-time clock (calendar targets are converted), see module doc."""
    if actual_active is None:
        return "not reached"
    r = actual_active / target_active
    if t.mode == "at_most":
        return "ok" if r <= 1 + t.tolerance else "too slow"
    if r < 1 - t.tolerance:
        return "too fast"
    if r > 1 + t.tolerance:
        return "too slow"
    return "ok"


def check_targets(cfg: EconomyConfig, result: SimResult) -> list[TargetCheck]:
    out = []
    data = cfg.model_dump()
    for t in cfg.pacing.targets:
        actual = result.milestone_hours(t.milestone, t.clock)
        target_active = t.hours if t.clock == "active" else active_hours_at(cfg, result.strategy, t.hours)
        lever = t.lever if isinstance(t.lever, str) else ", ".join(t.lever)
        actual_active = result.milestone_hours(t.milestone, "active")
        out.append(
            TargetCheck(
                milestone=t.milestone,
                description=t.description,
                clock=t.clock,
                mode=t.mode,
                tolerance=t.tolerance,
                target_h=t.hours,
                actual_h=actual,
                target_active_h=target_active,
                actual_active_h=actual_active,
                lever=lever,
                lever_value=lever_value(data, t.lever),
                status=_status(t, actual_active, target_active),
            )
        )
    return out


def _measure(data: dict[str, Any], strategy: str, milestone: str) -> float | None:
    cfg = config_from_dict(data)
    r = simulate(cfg, strategy, stop_when=milestone)
    return r.milestone_hours(milestone, "active")


def suggest(cfg: EconomyConfig, checks: list[TargetCheck]) -> list[TargetCheck]:
    """Fill elasticity and suggested factor for every off-target check (runs extra sims)."""
    data = cfg.model_dump()
    strategy = cfg.pacing.reference_strategy
    targets = {t.milestone: t for t in cfg.pacing.targets}
    for c in checks:
        if c.status == "ok":
            c.suggested_factor = 1.0
            continue
        t = targets[c.milestone]
        if c.actual_active_h is None:
            for f in (0.5, 0.2, 0.05):
                y = _measure(scale_lever(data, t.lever, f), strategy, c.milestone)
                if y is not None:
                    c.suggested_factor = f
                    c.note = (
                        f"not reached within the horizon; scaling the lever by x{f:g} reaches it at {y:.2f} h of play"
                    )
                    break
            else:
                c.note = "not reached even with the lever at x0.05: the blocker is elsewhere"
            continue
        p = 0.7 if c.status == "too slow" else 1 / 0.7
        y0 = c.actual_active_h
        y1 = _measure(scale_lever(data, t.lever, p), strategy, c.milestone)
        if y1 is None or y1 <= 0 or y0 <= 0:
            c.note = "lever change made the milestone unreachable"
            continue
        e = math.log(y1 / y0) / math.log(p)
        c.elasticity = e
        if e < 0.02:
            c.note = "milestone barely reacts to this lever; change something upstream"
            continue
        f = (c.target_active_h / y0) ** (1.0 / e)
        c.suggested_factor = min(max(f, 0.02), 50.0)
    return checks


@dataclass
class TuneStep:
    milestone: str
    lever: str
    factor: float
    before_active_h: float | None
    after_active_h: float | None
    target_active_h: float


@dataclass
class TuneResult:
    data: dict[str, Any]
    overrides: dict[str, float] = field(default_factory=dict)
    steps: list[TuneStep] = field(default_factory=list)


def auto_tune(cfg: EconomyConfig, max_iter: int = 8) -> TuneResult:
    """Sequentially tune every target's lever with a log-log secant search."""
    original = cfg.model_dump()
    data = copy.deepcopy(original)
    strategy = cfg.pacing.reference_strategy
    steps: list[TuneStep] = []
    for t in cfg.pacing.targets:
        goal = t.hours if t.clock == "active" else active_hours_at(cfg, strategy, t.hours)
        y0 = _measure(data, strategy, t.milestone)
        first = y0
        total = 1.0
        if y0 is None:  # make it reachable first
            for f in (0.5, 0.2, 0.05):
                y = _measure(scale_lever(data, t.lever, f), strategy, t.milestone)
                if y is not None:
                    data, total, y0 = scale_lever(data, t.lever, f), f, y
                    break
            if y0 is None:
                steps.append(TuneStep(t.milestone, str(t.lever), 1.0, None, None, goal))
                continue

        def ok(y: float | None, goal: float = goal, t: Target = t) -> bool:
            if y is None:
                return False
            r = y / goal
            if t.mode == "at_most":
                return r <= 1 + t.tolerance / 3
            return abs(r - 1) <= t.tolerance / 3

        x_prev, y_prev = 0.0, y0  # log-factor relative to `data`, measured time
        cur = data
        x = math.log(0.7 if y0 > goal else 1 / 0.7)
        for _ in range(max_iter):
            if ok(y_prev):
                break
            trial = scale_lever(data, t.lever, math.exp(x))
            y = _measure(trial, strategy, t.milestone)
            if y is None:
                x = (x + x_prev) / 2
                continue
            cur, y_new = trial, y
            if ok(y_new):
                x_prev, y_prev = x, y_new
                break
            tiny = 1e-9  # a milestone reached at t = 0 must not crash the log-log search
            dy = math.log(max(y_new, tiny)) - math.log(max(y_prev, tiny))
            dx = x - x_prev
            slope = dy / dx if abs(dx) > 1e-9 else 0.0
            x_prev, y_prev = x, y_new
            if slope < 0.02:
                x = x + (math.log(0.5) if y_new > goal else math.log(2.0))
            else:
                x = x + (math.log(goal) - math.log(max(y_new, tiny))) / slope
                x = max(min(x, x_prev + math.log(10)), x_prev - math.log(10))
        data = cur
        total *= math.exp(x_prev)
        steps.append(TuneStep(t.milestone, str(t.lever), total, first, y_prev, goal))

    overrides: dict[str, float] = {}
    for t in cfg.pacing.targets:
        for p in lever_paths(data, t.lever):
            old, new = get_path(original, p), get_path(data, p)
            if isinstance(new, (int, float)) and new != old:
                set_path(data, p, nice(float(new), 3))
                overrides[p] = get_path(data, p)
    return TuneResult(data, overrides, steps)


def apply_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(data)
    for p, v in overrides.items():
        set_path(new, p, v)
    return new


def format_yaml_number(v: float) -> str:
    """YAML-safe number text (PyYAML only reads scientific notation with a dot and a sign: 1.5e+7)."""
    if not math.isfinite(v):
        raise ValueError(f"cannot write non-finite number {v} into the config")
    if float(v).is_integer() and abs(v) < 1e6:
        return str(int(v))
    if v != 0 and (abs(v) >= 1e6 or abs(v) < 1e-4):
        exp = math.floor(math.log10(abs(v)))
        mant = f"{v / 10**exp:.3g}"
        if "." not in mant:
            mant += ".0"
        return f"{mant}e{exp:+d}"
    return repr(float(v))


def patch_yaml_text(text: str, overrides: dict[str, float]) -> str:
    """Write dotted-path overrides into block-style YAML text, keeping comments and layout.

    List items are located by their ``- id: <id>`` line, mapping keys by ``key:``; the value of
    the final key is replaced in place and an existing trailing comment is kept.
    """
    import re

    lines = text.split("\n")
    for path, value in overrides.items():
        parts = path.split(".")
        pos = 0
        indent = -1
        for k, part in enumerate(parts):
            last = k == len(parts) - 1
            found = None
            for i in range(pos, len(lines)):
                ln = lines[i]
                stripped = ln.lstrip(" ")
                ind = len(ln) - len(stripped)
                if stripped and not stripped.startswith("#") and ind <= indent and i > pos:
                    break  # left the current block
                if re.match(rf"-\s+id:\s*{re.escape(part)}\s*(#.*)?$", stripped):
                    found, indent = i, ind + 1
                    break
                if re.match(rf"{re.escape(part)}\s*:", stripped):
                    found, indent = i, ind
                    break
            if found is None:
                raise KeyError(f"cannot locate '{part}' of '{path}' in the YAML text")
            pos = found
            if last:
                ln = lines[found]
                m = re.match(rf"^(\s*(?:-\s+)?{re.escape(part)}\s*:\s*)([^#]*?)(\s*#.*)?$", ln)
                if not m:
                    raise KeyError(f"cannot rewrite line for '{path}': {ln!r}")
                lines[found] = m.group(1) + format_yaml_number(value) + (m.group(3) or "")
    return "\n".join(lines)
