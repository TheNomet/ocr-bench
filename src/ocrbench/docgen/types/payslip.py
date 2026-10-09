"""Payslip (lønnsslipp) with earnings/deductions table and a summary."""

from __future__ import annotations

import random
from datetime import date

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Monthly payslip from an employer to an employee",
    "fields": {
        "employer": {"type": "string", "description": "Employer company name"},
        "employee": {"type": "string", "description": "Employee full name"},
        "period": {"type": "string", "description": "Pay period, as printed (dd.mm.yyyy - dd.mm.yyyy)"},
        "gross_pay": {"type": "string", "description": "Gross pay for the period (brutto lønn)"},
        "tax_withheld": {"type": "string", "description": "Tax withheld (forskuddstrekk)"},
        "net_pay": {"type": "string", "description": "Net amount paid out"},
        "payment_date": {"type": "string", "description": "Payment date (dd.mm.yyyy)"},
        "account_number": {"type": "string", "description": "Employee bank account the pay is sent to"},
    },
}

EMPLOYERS = [
    "Bølgen Barnehage AS",
    "Fjelltopp Logistikk AS",
    "Kafé Måken AS",
    "Nordvik Byggservice AS",
    "Lysgården Hotell AS",
    "Grønnsaksgården Løken",
]
TITLES = ["Barnehagelærer", "Lagermedarbeider", "Kokk", "Tømrer", "Resepsjonist", "Sjåfør", "Gartner"]


def build(r: random.Random) -> Doc:
    employer = r.choice(EMPLOYERS)
    e_street = fake.street(r)
    e_pc, e_city = fake.city(r)
    emp, street, pc_city, _ = fake.person(r)
    start = date(2026, r.randint(1, 11), 1)
    end = fake.month_end(start)
    period = f"{fake.fd(start)} - {fake.fd(end)}"
    pay_day = fake.fd(date(start.year, start.month, r.choice([12, 15, 20, 25])))
    acct = fake.account(r)
    tax_pct = r.choice([28, 30, 31, 33, 35, 37])

    monthly = fake.money(r, 32000, 62000, 10000)
    hourly = round(monthly / 162.5)
    earn: list[tuple[str, str, str, str, int]] = [("1000", "Fastlønn", "1", fake.nok(monthly), monthly)]
    if r.random() < 0.7:
        h = r.randint(2, 18)
        rate = round(hourly * 1.5)
        earn.append(("1210", "Overtid 50 %", str(h), fake.nok(rate), h * rate))
    if r.random() < 0.5:
        h = r.randint(8, 40)
        rate = fake.money(r, 22, 65)
        earn.append(("1300", "Kveldstillegg", str(h), fake.nok(rate), h * rate))
    if start.month == 6:
        fp = round(monthly * 12 * 0.12)
        earn.append(("1700", "Feriepenger", "1", fake.nok(fp), fp))
    gross = sum(x[4] for x in earn)
    tax = int(round(gross * tax_pct / 100, -2))
    pension = round(gross * 0.02)
    ded: list[tuple[str, str, str, str, int]] = [
        ("5000", f"Skattetrekk tabell 7{r.randint(100, 199)}", "", "", -tax),
        ("5300", "Pensjon 2 %", "", "", -pension),
    ]
    if r.random() < 0.5:
        u = fake.money(r, 250, 600, 100)
        ded.append(("5400", "Fagforeningskontingent", "", "", -u))
    if r.random() < 0.4:
        k = fake.money(r, 150, 450, 100)
        ded.append(("5500", "Kantine", "", "", -k))
    net = gross + sum(x[4] for x in ded)
    other = -sum(x[4] for x in ded[1:])

    css = ".brand{font-size:14pt;font-weight:700} .summary .row{font-size:11pt}"
    doc = Doc("payslip", title=f"Lønnsslipp {fake.month_name(start)} {start.year}", css=css)
    doc.raw('<div class="flex"><div>')
    doc.lines([employer], cls="brand")
    doc.lines([e_street, f"{doc.num(e_pc)} {e_city}", f"Org.nr. {doc.num(fake.org_nr(r))}"], cls="small")
    doc.raw('</div><div class="right">')
    doc.h1("Lønnsslipp")
    doc.lines([f"{fake.month_name(start).capitalize()} {doc.num(str(start.year))}"])
    doc.raw("</div></div>")
    doc.raw('<div class="flex" style="margin-top:6mm"><div>')
    doc.lines([emp, street, pc_city])
    doc.raw('</div><div class="box">')
    doc.kv("Ansattnr.", doc.num(str(r.randint(100, 9999))))
    doc.kv("Stilling", r.choice(TITLES))
    doc.kv("Stillingsprosent", f"{doc.num(str(r.choice([60, 80, 100, 100, 100])))} %")
    doc.kv("Periode", f"{doc.num(fake.fd(start))} - {doc.num(fake.fd(end))}")
    doc.kv("Utbetalingsdato", doc.num(pay_day))
    doc.raw("</div></div>")

    doc.h2("Opptjening og trekk")
    rows = []
    for code, text, qty, rate, amount in earn + ded:
        for s in (code, qty, rate):
            if s:
                doc.num(s)
        rows.append([code, text, qty, rate, doc.num(fake.nok(amount))])
    doc.table(
        ["Lønnsart", "Beskrivelse", "Antall", "Sats", "Beløp"],
        rows,
        numeric={2, 3, 4},
        widths=["12%", "44%", "12%", "16%", "16%"],
    )

    doc.raw('<div class="flex"><div class="box summary">')
    doc.h3("Sammendrag")
    doc.kv("Brutto lønn", doc.num(fake.nok(gross)))
    doc.kv("Forskuddstrekk", doc.num(fake.nok(tax)))
    doc.kv("Andre trekk", doc.num(fake.nok(other)))
    doc.row("Netto utbetalt", doc.num(fake.nok(net)), bold=(0, 1))
    doc.kv("Utbetales til konto", doc.num(acct))
    doc.raw('</div><div class="box">')
    m = start.month
    doc.h3("Hittil i år")
    doc.kv("Brutto lønn", doc.num(fake.nok(gross * m - r.randint(0, 5000) * 100)))
    doc.kv("Forskuddstrekk", doc.num(fake.nok(tax * m)))
    doc.kv("Feriepengegrunnlag", doc.num(fake.nok(monthly * m)))
    doc.kv("Skatteprosent", f"{doc.num(str(tax_pct))} %")
    doc.raw("</div></div>")
    doc.p("Spørsmål om lønn rettes til lønningskontoret. Ta vare på lønnsslippen.", cls="footer")

    doc.field("employer", employer)
    doc.field("employee", emp)
    doc.field("period", period)
    doc.field("gross_pay", fake.nok(gross))
    doc.field("tax_withheld", fake.nok(tax))
    doc.field("net_pay", fake.nok(net))
    doc.field("payment_date", pay_day)
    doc.field("account_number", acct)
    return doc
