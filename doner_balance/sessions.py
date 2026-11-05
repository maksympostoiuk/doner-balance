"""Session model: when the player is in the game."""

from __future__ import annotations

import zlib

import numpy as np

from .config import SessionProfile


def session_rng(seed: int) -> np.random.Generator:
    """Own RNG stream for session variance, independent of the NPC event streams."""
    return np.random.default_rng([seed, zlib.crc32(b"sessions")])


def has_variance(profile: SessionProfile) -> bool:
    return bool(
        profile.session_jitter_minutes
        or profile.session_length_cv
        or profile.skip_session_chance
        or profile.skip_day_chance
    )


def session_schedule(
    profile: SessionProfile, days: float, rng: np.random.Generator | None = None
) -> list[tuple[float, float]]:
    """List of (start, end) calendar seconds for every session within ``days``.

    Sessions are spread evenly over the waking window [day_start_hour, day_end_hour).
    The game is installed at the start of the first session (t = 0), so all times are
    "seconds since install". The very first session may be longer (onboarding).

    With an ``rng`` and a profile that has variance, every nominal session is shifted by
    ``session_jitter_minutes`` (normal), its length varies by ``session_length_cv`` (normal,
    clipped to 0.25x..2x), and it is missed with ``skip_session_chance``; whole days are missed
    with ``skip_day_chance``. The install session is never moved or missed. Exactly four random
    numbers are drawn per nominal session, so a shorter horizon gives a prefix of a longer one.
    """
    horizon = days * 86400.0
    n = profile.sessions_per_day
    window = (profile.day_end_hour - profile.day_start_hour) * 3600.0
    spacing = window / n
    offset = profile.day_start_hour * 3600.0  # install moment = first session of day 0
    vary = rng is not None and has_variance(profile)
    out: list[tuple[float, float]] = []
    day = 0
    while True:
        day_skipped = False
        for i in range(n):
            start = day * 86400.0 + profile.day_start_hour * 3600.0 + i * spacing - offset
            if start >= horizon:
                return out
            minutes = profile.session_minutes
            if day == 0 and i == 0 and profile.first_session_minutes:
                minutes = profile.first_session_minutes
            if vary:
                assert rng is not None
                u_day, u_skip, z_start, z_len = rng.random(), rng.random(), rng.normal(), rng.normal()
                install = day == 0 and i == 0
                if not install:
                    if i == 0:
                        day_skipped = u_day < profile.skip_day_chance
                    if day_skipped or u_skip < profile.skip_session_chance:
                        continue
                    start += z_start * profile.session_jitter_minutes * 60.0
                    minutes *= float(np.clip(1.0 + profile.session_length_cv * z_len, 0.25, 2.0))
                if out:
                    start = max(start, out[-1][1])  # never overlap the previous session
                start = max(start, 0.0)
                if start >= horizon:
                    return out
            out.append((start, min(start + minutes * 60.0, horizon)))
        day += 1
