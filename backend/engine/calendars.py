"""Holiday calendars per country, used as forecast regressors (dates of moving holidays are
published calendars; Islamic dates are the Bangladesh observances and can shift by a day)."""
from __future__ import annotations

import datetime as dt

D = dt.date

EID_FITR = [D(2019, 6, 5), D(2020, 5, 25), D(2021, 5, 14), D(2022, 5, 3), D(2023, 4, 22), D(2024, 4, 11),
            D(2025, 3, 31), D(2026, 3, 20), D(2027, 3, 10), D(2028, 2, 27)]
EID_ADHA = [D(2019, 8, 12), D(2020, 8, 1), D(2021, 7, 21), D(2022, 7, 10), D(2023, 6, 29), D(2024, 6, 17),
            D(2025, 6, 7), D(2026, 5, 27), D(2027, 5, 17), D(2028, 5, 5)]
CHINESE_NEW_YEAR = [D(2019, 2, 5), D(2020, 1, 25), D(2021, 2, 12), D(2022, 2, 1), D(2023, 1, 22), D(2024, 2, 10),
                    D(2025, 1, 29), D(2026, 2, 17), D(2027, 2, 6), D(2028, 1, 26)]
EASTER = [D(2019, 4, 21), D(2020, 4, 12), D(2021, 4, 4), D(2022, 4, 17), D(2023, 4, 9), D(2024, 3, 31),
          D(2025, 4, 20), D(2026, 4, 5), D(2027, 3, 28), D(2028, 4, 16)]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def events(country: str, start: dt.date, end: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    """(holiday name, window start, window end): the days when trade is affected."""
    out: list[tuple[str, dt.date, dt.date]] = []
    years = range(start.year - 1, end.year + 2)
    c = (country or "").upper()
    if c == "BD":
        for e in EID_FITR:
            out.append(("Eid al-Fitr shopping", e - dt.timedelta(days=14), e))       # pre-Eid peak
            out.append(("Ramadan", e - dt.timedelta(days=30), e - dt.timedelta(days=15)))
        for e in EID_ADHA:
            out.append(("Eid al-Adha", e - dt.timedelta(days=7), e + dt.timedelta(days=2)))
    elif c == "CN":
        for e in CHINESE_NEW_YEAR:
            out.append(("Chinese New Year", e - dt.timedelta(days=7), e + dt.timedelta(days=10)))
        for y in years:
            out.append(("Singles Day (11.11)", dt.date(y, 11, 1), dt.date(y, 11, 12)))
            bf = _nth_weekday(y, 11, 4, 4)
            out.append(("Black Friday / Cyber Monday", bf - dt.timedelta(days=3), bf + dt.timedelta(days=4)))
            out.append(("Christmas shipping peak", dt.date(y, 12, 1), dt.date(y, 12, 20)))
    elif c in ("GB", "UK"):
        for e in EASTER:
            out.append(("Easter bank holidays", e - dt.timedelta(days=2), e + dt.timedelta(days=1)))
        for y in years:
            out.append(("Early May bank holiday", _nth_weekday(y, 5, 0, 1), _nth_weekday(y, 5, 0, 1)))
            out.append(("Spring bank holiday", _last_weekday(y, 5, 0), _last_weekday(y, 5, 0)))
            out.append(("Summer bank holiday", _last_weekday(y, 8, 0), _last_weekday(y, 8, 0)))
            out.append(("Christmas", dt.date(y, 12, 20), dt.date(y, 12, 28)))
            out.append(("Spring planting season", dt.date(y, 4, 1), dt.date(y, 5, 31)))
    return [(n, s, e) for n, s, e in out if e >= start - dt.timedelta(days=400) and s <= end + dt.timedelta(days=400)]
