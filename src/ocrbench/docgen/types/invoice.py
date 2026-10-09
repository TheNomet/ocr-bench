"""Invoice (faktura) from a small craftsman company to a private customer."""

from __future__ import annotations

import random
from datetime import timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Invoice from a company to a private customer",
    "fields": {
        "invoice_number": {"type": "string", "description": "Invoice number"},
        "invoice_date": {"type": "string", "description": "Invoice date (dd.mm.yyyy)"},
        "due_date": {"type": "string", "description": "Due date (dd.mm.yyyy)"},
        "kid": {"type": "string", "description": "KID payment reference number"},
        "account_number": {"type": "string", "description": "Account number to pay to (1234.56.78903)"},
        "subtotal": {"type": "string", "description": "Sum excluding VAT"},
        "vat": {"type": "string", "description": "VAT amount (25 %)"},
        "total_due": {"type": "string", "description": "Total amount to pay including VAT"},
    },
}

COMPANIES = [
    (
        "Hansen Rør & Bad AS",
        [
            ("Riving av eksisterende baderom", "timer", 650, 950),
            ("Membran og smøremembran", "m²", 290, 480),
            ("Flislegging vegg og gulv", "m²", 690, 1150),
            ("Montering av servant og toalett", "stk", 1800, 3200),
            ("Rørleggerarbeid", "timer", 850, 1150),
            ("Bortkjøring av avfall", "lass", 1200, 2400),
        ],
    ),
    (
        "Snekker Øvrebø AS",
        [
            ("Bytte av terrassebord", "m²", 450, 780),
            ("Impregnert trelast", "lm", 35, 79),
            ("Snekkerarbeid", "timer", 690, 950),
            ("Montering av rekkverk", "lm", 420, 690),
            ("Skruer og beslag", "pk", 120, 380),
            ("Kjøring", "stk", 450, 850),
        ],
    ),
    (
        "Elektro-Lie AS",
        [
            ("Elektrikerarbeid", "timer", 790, 1090),
            ("Nytt sikringsskap", "stk", 6500, 12500),
            ("Stikkontakt dobbel jordet", "stk", 190, 390),
            ("Downlights LED", "stk", 290, 590),
            ("Kabel 3G1,5", "m", 18, 35),
            ("Samsvarserklæring og dokumentasjon", "stk", 900, 1500),
        ],
    ),
    (
        "Malermester Nguyen",
        [
            ("Sparkling og sliping av vegger", "m²", 120, 220),
            ("Maling to strøk", "m²", 140, 260),
            ("Maling av tak", "m²", 160, 280),
            ("Malerarbeid", "timer", 620, 880),
            ("Maling og materiell", "pk", 900, 3400),
            ("Tildekking og rengjøring", "stk", 600, 1400),
        ],
    ),
]


def build(r: random.Random) -> Doc:
    company, catalog = r.choice(COMPANIES)
    c_street = fake.street(r)
    c_pc, c_city = fake.city(r)
    cust, street, pc_city, _ = fake.person(r)
    inv_no = str(r.randint(10000, 99999))
    inv_date = fake.rdate(r, span=280)
    due = inv_date + timedelta(days=r.choice([10, 14, 14, 20, 30]))
    kid = fake.kid(r, r.choice([10, 12, 15]))
    acct = fake.account(r)

    rows = []
    sub = 0
    for desc, unit_name, lo, hi in r.sample(catalog, r.randint(3, 6)):
        qty = r.randint(1, 4) if unit_name in ("stk", "lass", "pk") else r.randint(2, 40)
        unit = fake.money(r, lo, hi, 100)
        line = qty * unit
        sub += line
        rows.append((desc, f"{qty} {unit_name}", fake.nok(unit), fake.nok(line), str(qty)))
    vat = round(sub * 0.25)
    total = sub + vat

    css = ".brand{font-size:15pt;font-weight:700;color:#7a3e00} .meta .row{justify-content:space-between;gap:8mm}"
    doc = Doc("invoice", title=f"Faktura nr. {inv_no}", css=css)
    doc.raw('<div class="flex"><div>')
    doc.lines([company], cls="brand")
    doc.lines(
        [
            c_street,
            f"{doc.num(c_pc)} {c_city}",
            f"Org.nr. {doc.num(fake.org_nr(r))} MVA",
            f"Tlf. {doc.num(fake.phone(r))}",
        ],
        cls="small",
    )
    doc.raw('</div><div class="right">')
    doc.h1(f"Faktura nr. {doc.num(inv_no)}")
    doc.raw("</div></div>")
    doc.raw('<div class="flex" style="margin-top:8mm"><div>')
    doc.lines(["Faktura til:"], cls="small muted")
    doc.lines([cust, street, pc_city])
    doc.raw('</div><div class="meta box">')
    doc.kv("Fakturadato", doc.num(fake.fd(inv_date)))
    doc.kv("Forfallsdato", doc.num(fake.fd(due)))
    doc.kv("Kundenr.", doc.num(str(r.randint(1000, 9999))))
    doc.kv("KID", doc.num(kid))
    doc.kv("Kontonr.", doc.num(acct))
    doc.raw("</div></div>")
    doc.h2("Spesifikasjon")
    for _desc, _qty_s, unit_s, line_s, qty in rows:
        doc.num(qty)
        doc.num(unit_s)
        doc.num(line_s)
    doc.table(
        ["Beskrivelse", "Antall", "Enhetspris", "Beløp"],
        [list(x[:4]) for x in rows],
        numeric={1, 2, 3},
        widths=["52%", "14%", "17%", "17%"],
    )
    doc.raw('<div style="width:85mm;margin-left:auto" class="box">')
    doc.kv("Sum eks. mva", doc.num(fake.nok(sub)))
    doc.kv(f"MVA {doc.num('25')} %", doc.num(fake.nok(vat)))
    doc.row("Å betale", doc.num(fake.nok(total)), cls="spread big", bold=(0, 1))
    doc.raw("</div>")
    doc.p(
        f"Betalingsbetingelser: {doc.num(str((due - inv_date).days))} dager netto. Ved for sen betaling "
        "påløper forsinkelsesrente etter gjeldende sats. Husk å oppgi KID ved betaling.",
        cls="small",
    )
    doc.p("Vi gir garanti på utført arbeid i henhold til avtalen. Takk for oppdraget!", cls="small")

    doc.field("invoice_number", inv_no)
    doc.field("invoice_date", fake.fd(inv_date))
    doc.field("due_date", fake.fd(due))
    doc.field("kid", kid)
    doc.field("account_number", acct)
    doc.field("subtotal", fake.nok(sub))
    doc.field("vat", fake.nok(vat))
    doc.field("total_due", fake.nok(total))
    return doc
