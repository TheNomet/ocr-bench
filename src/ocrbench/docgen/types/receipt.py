"""Narrow monospace shop receipt."""

from __future__ import annotations

import random

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Shop receipt (kvittering) from a store",
    "fields": {
        "merchant": {"type": "string", "description": "Shop name at the top of the receipt"},
        "date": {"type": "string", "description": "Purchase date (dd.mm.yyyy)"},
        "time": {"type": "string", "description": "Purchase time (hh:mm)"},
        "total": {"type": "string", "description": "Total amount paid (TOTALT)"},
        "vat": {"type": "string", "description": "VAT amount included in the total"},
        "card_last4": {"type": "string", "description": "Last four digits of the payment card"},
        "item_count": {"type": "string", "description": "Number of items purchased, as printed"},
    },
}

SHOPS = {
    "food": (
        15,
        [
            "Matkroken Nordlys",
            "Fiskebutikken Bryggen",
            "Nærbutikken Solsiden",
            "Bakeriet på Hjørnet",
            "Dagligvare Fjellbu",
            "Grønt & Godt Torget",
        ],
        [
            ("Melk lett 1,75 l", 22, 32),
            ("Brød grovt", 29, 49),
            ("Kaffe 500 g", 69, 119),
            ("Smør 250 g", 32, 45),
            ("Epler 1 kg", 24, 39),
            ("Laks fersk", 89, 189),
            ("Ost hvit 1 kg", 99, 139),
            ("Egg 12 stk", 39, 59),
            ("Tomater 500 g", 19, 35),
            ("Havregryn 1 kg", 22, 34),
            ("Bananer", 12, 29),
            ("Kjøttdeig 400 g", 39, 69),
            ("Yoghurt jordbær", 15, 22),
            ("Rundstykker 6 stk", 29, 45),
            ("Appelsinjuice 1 l", 25, 42),
            ("Potetgull 250 g", 29, 39),
            ("Spaghetti 500 g", 14, 26),
        ],
    ),
    "hardware": (
        25,
        [
            "Jernvare Sørlandet",
            "Byggtorget Ås",
            "Verktøyhuset Bjørkelangen",
            "Hus & Hage Ørsta",
        ],
        [
            ("Skruer 4x40 200 stk", 79, 129),
            ("Maling hvit 2,7 l", 349, 599),
            ("Pensel 50 mm", 39, 89),
            ("Malertape 25 m", 49, 79),
            ("Batterier AA 8 pk", 69, 119),
            ("Lyspære LED E27", 39, 79),
            ("Hagehanske str. 9", 59, 129),
            ("Skjøteledning 5 m", 129, 249),
            ("Sparkel 0,5 l", 69, 119),
            ("Sandpapir 10 pk", 49, 89),
            ("Kabelstrips 100 stk", 39, 59),
            ("Blomsterjord 40 l", 69, 119),
        ],
    ),
}
PAYMENTS = ["BankAxept", "Visa", "Mastercard"]


def build(r: random.Random) -> Doc:
    kind = r.choice(["food", "food", "hardware"])
    rate, shops, catalog = SHOPS[kind]
    shop = r.choice(shops)
    street = fake.street(r)
    pc, city = fake.city(r)
    when = fake.fd(fake.rdate(r))
    t = fake.hhmm(r, 7, 22)
    picks = r.sample(catalog, r.randint(4, 9))
    lines = []
    total = 0
    count = 0
    for name, lo, hi in picks:
        qty = r.choice([1, 1, 1, 1, 2, 3])
        unit = fake.money(r, lo, hi, 10)
        amount = qty * unit
        total += amount
        count += qty
        lines.append((name, qty, unit, amount))
    vat = round(total * rate / (100 + rate))
    last4 = f"{r.randint(0, 9999):04d}"
    pay = r.choice(PAYMENTS)
    receipt_no = str(r.randint(1000, 99999))
    n_lines = len(lines) + sum(1 for x in lines if x[1] > 1) + 22
    height = int(30 + n_lines * 5.2)

    css = (
        ".page{padding:8mm 6mm;font-family:'Courier New',Courier,monospace;font-size:10pt;line-height:1.3}"
        ".page h2{text-align:center;font-size:13pt;margin:0 0 1mm;letter-spacing:0.5px}"
        ".c{text-align:center} .rule{border-top:1px dashed #333;margin:2mm 0}"
        ".row{gap:3mm;margin:0.3mm 0}.row span:last-child{white-space:nowrap}"
        ".tot{font-weight:700;font-size:11.5pt}.ind{padding-left:4mm}"
    )
    doc = Doc("receipt", title=f"Kvittering {shop}", css=css, page_css=f"size:80mm {height}mm;margin:0")
    doc.h2(shop.upper())
    doc.lines([street, f"{doc.num(pc)} {city}", f"Org.nr. {doc.num(fake.org_nr(r))} MVA"], cls="c")
    doc.raw('<div class="rule"></div>')
    doc.row(f"Dato {doc.num(when)}", f"Kl. {doc.num(t)}")
    doc.row(f"Kvittering nr. {doc.num(receipt_no)}", f"Kasse {doc.num(str(r.randint(1, 8)))}")
    doc.raw('<div class="rule"></div>')
    for name, qty, unit, amount in lines:
        if qty == 1:
            doc.row(name, doc.num(fake.nok(amount)))
        else:
            doc.row(name)
            doc.row(f"{doc.num(str(qty))} x {doc.num(fake.nok(unit))}", doc.num(fake.nok(amount)), cls="spread ind")
    doc.raw('<div class="rule"></div>')
    doc.row("Antall varer", doc.num(str(count)))
    doc.row("TOTALT", doc.num(fake.nok(total)), cls="spread tot")
    doc.raw('<div class="rule"></div>')
    doc.row(f"Herav MVA {doc.num(str(rate))} %", doc.num(fake.nok(vat)))
    doc.row("Grunnlag", doc.num(fake.nok(total - vat)))
    doc.raw('<div class="rule"></div>')
    doc.row("Betalt med", pay)
    doc.row("Kort", f"**** **** **** {doc.num(last4)}")
    doc.row("Kontaktløs", "Godkjent")
    doc.row("Ref.", doc.num(str(r.randint(100000, 999999))))
    doc.raw('<div class="rule"></div>')
    doc.lines(
        ["Takk for handelen!", f"Åpent alle dager {doc.num('07-23')}", f"Bytterett {doc.num('30')} dager"], cls="c"
    )

    doc.field("merchant", shop.upper())
    doc.field("date", when)
    doc.field("time", t)
    doc.field("total", fake.nok(total))
    doc.field("vat", fake.nok(vat))
    doc.field("card_last4", last4)
    doc.field("item_count", str(count))
    return doc
