"""Holiday-preferences questionnaire: boxed key/value block, checkbox grids, a yes/no line, signature."""

from __future__ import annotations

import random
from datetime import timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Filled-in holiday preferences questionnaire (ferieundersøkelse)",
    "fields": {
        "name": {"type": "string", "description": "Name of the person filling in the form"},
        "destination": {"type": "string", "description": "Preferred destination"},
        "date_from": {"type": "string", "description": "Desired travel start date (dd.mm.yyyy)"},
        "date_to": {"type": "string", "description": "Desired travel end date (dd.mm.yyyy)"},
        "travellers": {"type": "string", "description": "Number of travellers"},
        "budget_per_person": {"type": "string", "description": "Budget per person in NOK, as printed"},
        "activities": {"type": "list", "description": "Activities whose checkbox is checked (☑)"},
        "accommodation": {"type": "list", "description": "Accommodation types whose checkbox is checked (☑)"},
        "pets": {"type": "string", "description": "Answer to 'Reiser du med kjæledyr?': 'Ja' or 'Nei'"},
        "signed_date": {"type": "string", "description": "Date next to the signature (dd.mm.yyyy)"},
    },
}

CLUBS = ["Reiseklubben Solgløtt", "Feriefellesskapet Bølgen", "Turlaget Måkeskrik", "Reiselyst Medlemsforening"]
DESTINATIONS = [
    "Lofoten",
    "Kreta",
    "Gran Canaria",
    "Roma",
    "Tromsø",
    "Island",
    "Mallorca",
    "Geiranger",
    "København",
    "Lisboa",
    "Hemsedal",
    "Thailand",
    "Skottland",
    "Dubrovnik",
]
ACTIVITIES = [
    "Bading",
    "Fjelltur",
    "Museum",
    "Skiferie",
    "Cruise",
    "Storbyferie",
    "Sykkeltur",
    "Fisketur",
    "Konsert",
    "Spa",
    "Matopplevelser",
    "Dykking",
]
ACCOMMODATION = ["Hotell", "Leilighet", "Hytte", "Camping", "Bed & Breakfast", "Bobil"]
CHECK, BOX = "☑", "☐"


def _grid(doc: Doc, options: list[str], checked: set[str], per_row: int) -> None:
    for i in range(0, len(options), per_row):
        cells = [f"{CHECK if o in checked else BOX} {o}" for o in options[i : i + per_row]]
        doc.row(*cells, cls="grid")


def build(r: random.Random) -> Doc:
    who, street, pc_city, city = fake.person(r)
    club = r.choice(CLUBS)
    dest = r.choice(DESTINATIONS)
    d0 = fake.rdate(r, span=280)
    d1 = d0 + timedelta(days=r.randint(4, 21))
    trav = str(r.randint(1, 6))
    budget = fake.nok(fake.money(r, 4000, 30000, 50000))
    acts = r.sample(ACTIVITIES, r.randint(1, 5))
    accs = r.sample(ACCOMMODATION, r.randint(1, 2))
    pets = r.choice(["Ja", "Nei", "Nei"])
    signed = fake.fd(d0 - timedelta(days=r.randint(20, 120)))
    year = str(d0.year)

    css = (
        ".page{font-size:11pt}"
        ".box .row{border-bottom:1px dotted #888;padding:1.2mm 0;justify-content:flex-start}"
        ".box .row span:first-child{width:62mm;color:#333}"
        ".box .row span:last-child{font-family:'Bradley Hand','Marker Felt','Comic Sans MS',cursive;font-size:12.5pt;color:#1a2a6b}"
        ".row.grid{margin:1.2mm 0;font-size:11pt}"
        ".q{margin-top:6mm}.q span:first-child{font-weight:600;margin-right:6mm}"
        ".head{border-bottom:3px solid #2a6f97;padding-bottom:2mm;margin-bottom:4mm}"
        ".head h1{color:#2a6f97;margin:0}"
    )
    doc = Doc("holiday_survey", title=f"Ferieundersøkelse {year}", css=css)
    doc.raw('<div class="head flex"><div>')
    doc.h1(f"Ferieundersøkelse {doc.num(year)}")
    doc.raw('</div><div class="right">')
    doc.lines([club, f"Skjema nr. {doc.num(str(r.randint(100, 9999)))}"], cls="small")
    doc.raw("</div></div>")
    doc.p(
        "Vi ønsker å bli bedre kjent med medlemmenes ferieønsker før vi planlegger neste års fellesturer. "
        "Fyll ut skjemaet og lever det i resepsjonen eller send det i posten. Alle svar er frivillige.",
        cls="small",
    )

    doc.h2("1. Om deg og reisen")
    doc.raw('<div class="box">')
    doc.kv("Navn", who, cls="")
    doc.kv("Adresse", f"{street}, {pc_city}", cls="")
    doc.kv("Ønsket reisemål", dest, cls="")
    doc.kv("Reise fra (dato)", doc.num(fake.fd(d0)), cls="")
    doc.kv("Reise til (dato)", doc.num(fake.fd(d1)), cls="")
    doc.kv("Antall reisende", doc.num(trav), cls="")
    doc.kv("Budsjett per person (NOK)", doc.num(budget), cls="")
    doc.raw("</div>")

    doc.h2("2. Hvilke aktiviteter er du interessert i?")
    doc.p("Kryss av for alt som passer.", cls="small muted")
    _grid(doc, ACTIVITIES, set(acts), 3)

    doc.h2("3. Foretrukket overnatting")
    _grid(doc, ACCOMMODATION, set(accs), 3)

    doc.row(
        "Reiser du med kjæledyr?",
        f"{CHECK if pets == 'Ja' else BOX} Ja",
        f"{CHECK if pets == 'Nei' else BOX} Nei",
        cls="q",
    )

    doc.h2("4. Samtykke")
    doc.p(
        "Jeg bekrefter at opplysningene over er riktige, og at klubben kan kontakte meg om turer som passer "
        "ønskene mine. Opplysningene slettes etter at planleggingen er ferdig.",
        cls="small",
    )
    doc.raw('<div class="flex" style="margin-top:6mm"><div>')
    doc.sig(f"{city}, {doc.num(signed)}")
    doc.lines(["Sted og dato"], cls="sigline")
    doc.raw("</div><div>")
    doc.sig(who)
    doc.lines(["Underskrift"], cls="sigline")
    doc.raw("</div></div>")
    doc.p(f"Takk for svaret! Spørsmål kan rettes til styret på telefon {doc.num(fake.phone(r))}.", cls="footer")

    doc.field("name", who)
    doc.field("destination", dest)
    doc.field("date_from", fake.fd(d0))
    doc.field("date_to", fake.fd(d1))
    doc.field("travellers", trav)
    doc.field("budget_per_person", budget)
    doc.field("activities", [a for a in ACTIVITIES if a in acts])
    doc.field("accommodation", [a for a in ACCOMMODATION if a in accs])
    doc.field("pets", pets)
    doc.field("signed_date", signed)
    return doc
