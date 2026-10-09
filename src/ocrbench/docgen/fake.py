"""Fake-but-plausible Norwegian data and formatting helpers.

Everything here is invented. Amounts are handled as integer øre (1/100 NOK) so sums
printed on a document always add up exactly.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

FIRST = [
    "Kari",
    "Ola",
    "Ingrid",
    "Lars",
    "Sigrid",
    "Håkon",
    "Åse",
    "Bjørn",
    "Thi Lan",
    "Mohammed",
    "Øystein",
    "Ragnhild",
    "Siv",
    "Jørgen",
    "Marte",
    "Agnieszka",
    "Fatima",
    "Erik",
    "Solveig",
    "Tor Even",
    "Maja",
    "Ahmed",
    "Linnea",
    "Kjærsti",
    "Pål",
    "Aurora",
    "Jonas",
    "Mirela",
]
LAST = [
    "Nordmann",
    "Hansen",
    "Johansen",
    "Ødegård",
    "Berg",
    "Sæther",
    "Nguyen",
    "Haugen",
    "Lie",
    "Ali",
    "Strøm",
    "Bakke",
    "Kowalski",
    "Andersson",
    "Åsheim",
    "Kvalsvik",
    "Fløtten",
    "Mæland",
    "Okafor",
    "Østby",
    "Rønning",
    "Dahl",
    "Bråten",
    "Sørensen",
    "Popescu",
    "Ruud",
]
STREETS = [
    "Storgata",
    "Kirkeveien",
    "Bjørnstjerne Bjørnsons gate",
    "Fjordveien",
    "Åsveien",
    "Sjøgata",
    "Tollbugata",
    "Granstubben",
    "Løvåsveien",
    "Kongens gate",
    "Trollhaugen",
    "Bekkestien",
    "Måkeveien",
    "Ærfuglveien",
    "Skolegata",
    "Lindeveien",
]
CITIES = [
    ("0150", "Oslo"),
    ("0468", "Oslo"),
    ("5003", "Bergen"),
    ("7011", "Trondheim"),
    ("4006", "Stavanger"),
    ("9008", "Tromsø"),
    ("3011", "Drammen"),
    ("4610", "Kristiansand"),
    ("2317", "Hamar"),
    ("6002", "Ålesund"),
    ("8006", "Bodø"),
    ("1606", "Fredrikstad"),
]
MONTHS = [
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
]


def name(r: random.Random) -> str:
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def street(r: random.Random) -> str:
    return f"{r.choice(STREETS)} {r.randint(1, 140)}{r.choice(['', '', '', ' B', ' A'])}"


def city(r: random.Random) -> tuple[str, str]:
    return r.choice(CITIES)


def person(r: random.Random) -> tuple[str, str, str, str]:
    """(name, street, 'postcode city', city)"""
    pc, c = city(r)
    return name(r), street(r), f"{pc} {c}", c


def nok(cents: int) -> str:
    """Norwegian amount format from øre: 1250000 -> '12 500,00'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    kr, ore = divmod(cents, 100)
    return f"{sign}{kr:,}".replace(",", " ") + f",{ore:02d}"


def money(r: random.Random, lo: float, hi: float, round_to: int = 1) -> int:
    """Random amount in øre between lo and hi kroner, rounded to `round_to` øre."""
    c = r.randint(int(lo * 100), int(hi * 100))
    return c - c % round_to


def fd(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def rdate(r: random.Random, start: date = date(2026, 1, 1), span: int = 300) -> date:
    return start + timedelta(days=r.randint(0, span))


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def month_name(d: date) -> str:
    return MONTHS[d.month - 1]


def account(r: random.Random) -> str:
    """Norwegian-style 11-digit account number with a valid mod-11 check digit: 1234.56.78903."""
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    while True:
        digits = [r.randint(1, 9)] + [r.randint(0, 9) for _ in range(9)]
        rest = sum(d * w for d, w in zip(digits, weights, strict=True)) % 11
        check = 0 if rest == 0 else 11 - rest
        if check == 10:
            continue
        s = "".join(map(str, digits)) + str(check)
        return f"{s[:4]}.{s[4:6]}.{s[6:]}"


def kid(r: random.Random, length: int = 12) -> str:
    """Payment reference with a Luhn (mod-10) check digit."""
    body = [r.randint(0, 9) for _ in range(length - 1)]
    total = 0
    for i, d in enumerate(reversed(body)):
        x = d * 2 if i % 2 == 0 else d
        total += x - 9 if x > 9 else x
    return "".join(map(str, body)) + str((10 - total % 10) % 10)


def org_nr(r: random.Random) -> str:
    return f"{r.randint(910, 998)} {r.randint(100, 999)} {r.randint(100, 999)}"


def phone(r: random.Random) -> str:
    return f"{r.choice([4, 9])}{r.randint(0, 9)}{r.randint(0, 9)} {r.randint(10, 99)} {r.randint(100, 999)}"


def hhmm(r: random.Random, lo: int = 8, hi: int = 21) -> str:
    return f"{r.randint(lo, hi):02d}:{r.randint(0, 59):02d}"
