"""Two-column housing co-op (borettslag) newsletter."""

from __future__ import annotations

import random
from datetime import timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Newsletter from a housing co-op board to residents",
    "fields": {
        "issue_date": {"type": "string", "description": "Publication date of this issue (dd.mm.yyyy)"},
        "meeting_date": {
            "type": "string",
            "description": "Date of the annual residents meeting (årsmøte) (dd.mm.yyyy)",
        },
        "new_fee": {"type": "string", "description": "New monthly common cost for the example flat, in NOK"},
        "dugnad_date": {"type": "string", "description": "Date of the spring/autumn dugnad (dd.mm.yyyy)"},
    },
}

COOPS = ["Solbakken", "Furulund", "Kvernhuset", "Bjørkeli", "Måkeberget", "Elvebredden", "Tårnåsen"]
AREAS = [
    "Lekeplassen",
    "Sykkelboden",
    "Bed ved inngangen",
    "Parkeringsplassen",
    "Søppelhuset",
    "Gangstiene",
    "Grillplassen",
    "Fellesrommet",
]


def build(r: random.Random) -> Doc:
    coop = f"{r.choice(COOPS)} Borettslag"
    issue = fake.rdate(r, span=250)
    nr = str(r.randint(1, 6))
    dugnad = issue + timedelta(days=r.randint(10, 30))
    meeting = issue + timedelta(days=r.randint(25, 60))
    fee_from = fake.fd((issue + timedelta(days=75)).replace(day=1))
    increase = fake.money(r, 150, 600, 2500)
    old_fee = fake.money(r, 3200, 6800, 1000)
    new_fee = fake.nok(old_fee + increase)
    guest = fake.nok(fake.money(r, 20, 60, 500))
    park = fake.nok(fake.money(r, 250, 650, 2500))
    rooms = r.choice(["toroms", "treroms", "fireroms"])

    css = (
        ".mast{border-bottom:3px double #2d5d34;margin-bottom:4mm;padding-bottom:2mm}"
        ".mast h1{font-size:24pt;color:#2d5d34;margin:0;font-family:Georgia,'Times New Roman',serif}"
        ".cols{column-count:2;column-gap:9mm;column-rule:1px solid #ccc;font-size:10pt;text-align:justify}"
        ".cols > *{break-inside:avoid}"
        ".cols h2{font-family:Georgia,'Times New Roman',serif;color:#2d5d34;font-size:13pt;margin:0 0 1.5mm}"
        ".cols h2:not(:first-child){margin-top:4mm}"
        ".cols table{font-size:8.5pt;text-align:left}"
        ".hl{background:#eef5ee;border-left:3px solid #2d5d34;padding:2mm 3mm}"
    )
    doc = Doc("newsletter", title=f"Nytt fra {coop} nr. {nr}", css=css)
    doc.raw('<div class="mast">')
    doc.h1(f"Nytt fra {coop}")
    doc.row(f"Nr. {doc.num(nr)} – {doc.num(str(issue.year))}", f"Utgitt {doc.num(fake.fd(issue))}", cls="spread small")
    doc.raw("</div>")

    doc.raw('<div class="cols">')
    doc.h2("Vårdugnad" if issue.month < 7 else "Høstdugnad")
    doc.p(
        f"Styret inviterer alle beboere til dugnad {doc.num(fake.fd(dugnad))} fra klokken {doc.num('10:00')}. "
        "Vi skal rake løv, beise benkene og rydde i sykkelboden. Styret sørger for pølser, kaffe og "
        "boller når arbeidet er gjort, og barna får egne oppgaver ved lekeplassen."
    )
    doc.p("Oversikt over hvem som har ansvar for de ulike områdene:")
    rows = []
    hour = 10
    for area in r.sample(AREAS, r.randint(3, 5)):
        span = f"{hour:02d}:00-{hour + 2:02d}:00"
        rows.append([area, f"Oppgang {r.choice('ABCDE')}", doc.num(span)])
        hour = 10 if hour >= 12 else hour + 1
    doc.table(["Område", "Ansvar", "Tid"], rows, widths=["44%", "28%", "28%"])
    doc.p(
        "Containere for hageavfall og grovavfall står ved innkjøringen hele helgen. Farlig avfall som "
        "maling og batterier skal ikke kastes i containeren."
    )

    doc.h2("Nye parkeringsregler")
    doc.p(
        f"Fra neste måned blir gjesteparkeringen ved blokk B avgiftsbelagt med kr {doc.num(guest)} per døgn. "
        f"Fast plass i garasjeanlegget koster kr {doc.num(park)} per måned, og det er fortsatt ventetid. "
        "Biler uten gyldig parkeringsbevis vil bli bortslept for eiers regning."
    )

    doc.h2("Booking av vaskeriet")
    doc.p(
        "Det nye vaskeriet i kjelleren er nå klart. Maskinene bookes på tavlen ved døren eller i "
        f"beboerappen, i økter på to timer mellom klokken {doc.num('07:00')} og {doc.num('22:00')}. "
        "Hver leilighet kan ha inntil tre bookinger per uke. Husk å tørke av trommelen og tømme lofilteret "
        "etter bruk."
    )

    doc.h2("Felleskostnader og årsmøte")
    doc.p(
        f"Fra {doc.num(fee_from)} øker felleskostnadene med kr {doc.num(fake.nok(increase))} per måned. "
        "Økningen skyldes høyere kommunale avgifter og forsikring.",
        cls="",
    )
    doc.p(f"Nye felleskostnader for en {rooms} leilighet: kr {doc.num(new_fee)} per måned.", cls="hl")
    doc.p(
        f"Det ordinære årsmøtet holdes {doc.num(fake.fd(meeting))} klokken {doc.num('19:00')} i "
        "fellesrommet. Saker som ønskes behandlet må være levert til styret senest fire uker før møtet."
    )
    doc.p(
        f"Hilsen styret i {coop}. Henvendelser kan sendes til styreleder på telefon {doc.num(fake.phone(r))}.",
        cls="small",
    )
    doc.raw("</div>")

    doc.field("issue_date", fake.fd(issue))
    doc.field("meeting_date", fake.fd(meeting))
    doc.field("new_fee", new_fee)
    doc.field("dugnad_date", fake.fd(dugnad))
    return doc
