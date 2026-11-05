"""Session model: when the player is in the game."""

from __future__ import annotations

from .config import SessionProfile


def session_schedule(profile: SessionProfile, days: float) -> list[tuple[float, float]]:
    """List of (start, end) calendar seconds for every session within ``days``.

    Sessions are spread evenly over the waking window [day_start_hour, day_end_hour).
    The game is installed at the start of the first session (t = 0), so all times are
    "seconds since install". The very first session may be longer (onboarding).
    """
    horizon = days * 86400.0
    n = profile.sessions_per_day
    window = (profile.day_end_hour - profile.day_start_hour) * 3600.0
    spacing = window / n
    offset = profile.day_start_hour * 3600.0  # install moment = first session of day 0
    out: list[tuple[float, float]] = []
    day = 0
    while True:
        for i in range(n):
            start = day * 86400.0 + profile.day_start_hour * 3600.0 + i * spacing - offset
            if start >= horizon:
                return out
            minutes = profile.session_minutes
            if day == 0 and i == 0 and profile.first_session_minutes:
                minutes = profile.first_session_minutes
            out.append((start, min(start + minutes * 60.0, horizon)))
        day += 1
