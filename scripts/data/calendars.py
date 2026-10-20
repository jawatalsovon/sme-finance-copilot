"""Hand-set calendars (no `holidays` dependency). ASSUMPTIONS from public calendars; verify before quoting.

Bangladesh Eid dates follow the national moon-sighting announcement (a day later than Saudi in most years).
"""
from __future__ import annotations

import datetime as dt

D = dt.date

# Bangladesh (assumed from public calendars)
EID_FITR = {2020: D(2020, 5, 25), 2021: D(2021, 5, 14), 2022: D(2022, 5, 3), 2023: D(2023, 4, 22),
            2024: D(2024, 4, 11), 2025: D(2025, 3, 31), 2026: D(2026, 3, 21)}
EID_ADHA = {2020: D(2020, 8, 1), 2021: D(2021, 7, 21), 2022: D(2022, 7, 10), 2023: D(2023, 6, 29),
            2024: D(2024, 6, 17), 2025: D(2025, 6, 7), 2026: D(2026, 5, 28)}
# China: Spring Festival day 1
CNY = {2020: D(2020, 1, 25), 2021: D(2021, 2, 12), 2022: D(2022, 2, 1), 2023: D(2023, 1, 22),
       2024: D(2024, 2, 10), 2025: D(2025, 1, 29), 2026: D(2026, 2, 17)}
# Amazon-style summer sale (assumed dates; 2026 is a placeholder)
PRIME_DAY = {2020: D(2020, 10, 13), 2021: D(2021, 6, 21), 2022: D(2022, 7, 12), 2023: D(2023, 7, 11),
             2024: D(2024, 7, 16), 2025: D(2025, 7, 8), 2026: D(2026, 7, 14)}


def easter(y: int) -> dt.date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def _nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> dt.date:
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def uk_bank_holidays(y: int) -> set[dt.date]:
    out: set[dt.date] = set()
    ny = dt.date(y, 1, 1)
    out.add(ny if ny.weekday() < 5 else ny + dt.timedelta(days=7 - ny.weekday()))
    e = easter(y)
    out |= {e - dt.timedelta(days=2), e + dt.timedelta(days=1)}
    may_early = _nth_weekday(y, 5, 0, 1)
    spring = _last_weekday(y, 5, 0)
    if y == 2020:
        may_early = dt.date(2020, 5, 8)
    if y == 2023:
        out.add(dt.date(2023, 5, 8))
    if y == 2022:
        spring = dt.date(2022, 6, 2)
        out.add(dt.date(2022, 6, 3))
        out.add(dt.date(2022, 9, 19))
    out |= {may_early, spring, _last_weekday(y, 8, 0)}
    xmas, box = dt.date(y, 12, 25), dt.date(y, 12, 26)
    if xmas.weekday() == 5:
        out |= {xmas + dt.timedelta(days=2), box + dt.timedelta(days=2)}
    elif xmas.weekday() == 6:
        out |= {xmas + dt.timedelta(days=1), box + dt.timedelta(days=1)}
    elif xmas.weekday() == 4:
        out |= {xmas, box + dt.timedelta(days=2)}
    else:
        out |= {xmas, box}
    return out


def uk_mothers_day(y: int) -> dt.date:
    """Mothering Sunday: 3 weeks before Easter Sunday."""
    return easter(y) - dt.timedelta(weeks=3)


def black_friday(y: int) -> dt.date:
    return _nth_weekday(y, 11, 3, 4) + dt.timedelta(days=1)
