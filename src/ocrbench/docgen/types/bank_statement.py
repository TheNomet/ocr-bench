"""Monthly account statement (kontoutskrift) with a transaction table."""

from __future__ import annotations

import random
from datetime import date, timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Monthly bank account statement for a private customer",
    "fields": {
        "account_number": {"type": "string", "description": "Account number (format 1234.56.78903)"},
        "period_start": {"type": "string", "description": "First day of the statement period (dd.mm.yyyy)"},
        "period_end": {"type": "string", "description": "Last day of the statement period (dd.mm.yyyy)"},
        "opening_balance": {"type": "string", "description": "Opening balance (inngående saldo)"},
        "closing_balance": {"type": "string", "description": "Closing balance (utgående saldo)"},
        "transaction_count": {"type": "string", "description": "Number of transactions listed"},
    },
}

BANKS = ["Fjellbygd Sparebank", "Kystbanken Nord", "Elvedalen Sparebank", "Bygdebanken Vest"]
EMPLOYERS = ["Fjordlys Montasje AS", "Havbris Kafé AS", "Solberg Barnehage", "Nordvik Logistikk AS"]
SHOPS = ["Matkroken", "Nærbutikken", "Jernvare Sørlandet", "Bakeriet", "Apotek Sentrum", "Sportshuset", "Bokhandelen"]
DEBITS = [
    ("Varekjøp {shop}", 49, 1800, 6),
    ("Vipps *{first}", 50, 900, 3),
    ("Overføring til sparekonto", 500, 5000, 1),
    ("Strøm Lysglimt Energi", 400, 2600, 1),
    ("Mobilabonnement Prat", 199, 599, 1),
    ("Treningssenter Puls", 299, 699, 1),
    ("Forsikring Trygghjem", 300, 1400, 1),
    ("Minibank uttak", 200, 2000, 1),
    ("Strømmetjeneste Kino+", 99, 199, 1),
    ("Buss månedskort", 500, 900, 1),
]


def build(r: random.Random) -> Doc:
    holder, street, pc_city, _ = fake.person(r)
    bank = r.choice(BANKS)
    acct = fake.account(r)
    start = date(2026, r.randint(1, 9), 1)
    end = fake.month_end(start)
    opening = fake.money(r, 18000, 60000)
    n = r.randint(15, 26)

    # Fixed monthly items, then random everyday purchases, spread over the month.
    tx: list[tuple[date, str, int]] = [
        (start + timedelta(days=r.randint(0, 2)), "Husleie", -fake.money(r, 7000, 16000, 100)),
        (start + timedelta(days=r.randint(10, 14)), f"Lønn {r.choice(EMPLOYERS)}", fake.money(r, 26000, 48000)),
    ]
    used: set[str] = set()  # monthly bills/subscriptions appear at most once
    while len(tx) < n:
        if r.random() < 0.12:
            tx.append(
                (
                    start + timedelta(days=r.randint(0, end.day - 1)),
                    f"Vipps fra {r.choice(fake.FIRST)}",
                    fake.money(r, 100, 1500, 100),
                )
            )
            continue
        avail = [d for d in DEBITS if d[3] > 1 or d[0] not in used]
        tmpl, lo, hi, w = r.choices(avail, weights=[d[3] for d in avail])[0]
        used.add(tmpl)
        text = tmpl.format(shop=r.choice(SHOPS), first=r.choice(fake.FIRST))
        tx.append((start + timedelta(days=r.randint(0, end.day - 1)), text, -fake.money(r, lo, hi)))
    tx.sort(key=lambda x: x[0])

    doc = Doc(
        "bank_statement",
        title=f"Kontoutskrift {fake.month_name(start)} {start.year}",
        css="table{font-size:9pt} td,th{padding:1.6px 5px}",
    )
    doc.raw('<div class="flex"><div>')
    doc.h1("Kontoutskrift")
    doc.lines([holder, street, pc_city])
    doc.raw('</div><div class="right">')
    doc.lines([bank, "Kundeservice", f"Tlf. {doc.num(fake.phone(r))}"], cls="strong")
    doc.raw("</div></div>")
    doc.raw('<div class="box">')
    doc.kv("Kontonummer", doc.num(acct))
    doc.kv("Kontotype", "Brukskonto")
    doc.kv("Periode", f"{doc.num(fake.fd(start))} - {doc.num(fake.fd(end))}")
    doc.kv("Inngående saldo", doc.num(fake.nok(opening)))
    doc.raw("</div>")

    bal = opening
    rows = []
    for d, text, amt in tx:
        bal += amt
        inn = doc.num(fake.nok(amt)) if amt > 0 else ""
        ut = doc.num(fake.nok(-amt)) if amt < 0 else ""
        rows.append([doc.num(fake.fd(d)), text, inn, ut, doc.num(fake.nok(bal))])
    doc.table(
        ["Dato", "Forklaring", "Inn", "Ut", "Saldo"],
        rows,
        numeric={2, 3, 4},
        widths=["16%", "42%", "14%", "14%", "14%"],
    )
    doc.raw('<div class="box">')
    doc.kv("Antall transaksjoner", doc.num(str(len(rows))))
    doc.kv("Sum inn", doc.num(fake.nok(sum(a for _, _, a in tx if a > 0))))
    doc.kv("Sum ut", doc.num(fake.nok(-sum(a for _, _, a in tx if a < 0))))
    doc.row("Utgående saldo", doc.num(fake.nok(bal)), bold=(0, 1))
    doc.raw("</div>")
    doc.p(
        f"Kontoutskriften er laget {doc.num(fake.fd(end + timedelta(days=1)))}. Ta kontakt med oss "
        "innen rimelig tid dersom du finner feil i oversikten.",
        cls="small",
    )

    doc.field("account_number", acct)
    doc.field("period_start", fake.fd(start))
    doc.field("period_end", fake.fd(end))
    doc.field("opening_balance", fake.nok(opening))
    doc.field("closing_balance", fake.nok(bal))
    doc.field("transaction_count", str(len(rows)))
    return doc
