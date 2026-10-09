"""Personal letter: prose with a sender block, a date, and a few amounts/dates/phone numbers in sentences."""

from __future__ import annotations

import random
from datetime import date, timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Personal letter written by a private person",
    "fields": {
        "sender_name": {"type": "string", "description": "Full name of the person who wrote the letter"},
        "date": {"type": "string", "description": "Date the letter was written (dd.mm.yyyy)"},
        "recipient": {
            "type": "string",
            "description": "Name of the addressee in the recipient block",
            "optional": True,
        },
        "amounts": {"type": "list", "description": "All money amounts mentioned in the letter, as printed"},
        "phone": {"type": "string", "description": "Sender's phone number"},
    },
}

LANDLORDS = ["Bergheim Eiendom AS", "Utleie Vest AS", "Lyngstad Boliger AS"]
CLUBS = ["Fjellbygda Idrettslag", "Sjøsprøyt Svømmeklubb", "Ørnen Tennisklubb", "Nordlyset Orienteringslag"]


def _landlord(doc: Doc, r: random.Random, me: str, addr: str, when: date) -> tuple[str, list[str]]:
    rec = r.choice(LANDLORDS + [fake.name(r)])
    d0 = when - timedelta(days=r.randint(1, 10))
    a1 = fake.nok(fake.money(r, 1500, 6500, 100))
    a2 = fake.nok(fake.money(r, 350, 1800, 100))
    doc.h1("Vannlekkasje på badet")
    doc.p("Hei," if rec.endswith(" AS") else f"Hei {rec.split()[0]},")
    doc.p(
        f"Jeg skriver for å melde fra om en vannlekkasje i leiligheten i {addr}. Natt til "
        f"{doc.num(fake.fd(d0))} oppdaget jeg at det dryppet fra røret under vasken på badet, og "
        "gulvet rundt toalettet var vått da jeg sto opp. Jeg har stengt hovedkranen og satt ut en bøtte."
    )
    doc.p(
        f"En rørlegger kom innom dagen etter og anslo at reparasjonen vil koste rundt kr {doc.num(a1)}. "
        f"I tillegg har jeg betalt kr {doc.num(a2)} for leie av avfukter, og kvitteringen legger jeg ved."
    )
    doc.p(
        "Jeg ber om at dere tar kontakt så snart som mulig slik at vi kan avtale når arbeidet kan gjøres. "
        f"Jeg er hjemme de fleste ettermiddager etter klokken {doc.num('16:00')}."
    )
    return rec, [a1, a2]


def _neighbour(doc: Doc, r: random.Random, me: str, addr: str, when: date) -> tuple[None, list[str]]:
    d0 = when + timedelta(days=r.randint(12, 40))
    a1 = fake.nok(fake.money(r, 100, 300, 5000))
    a2 = fake.nok(fake.money(r, 800, 2500, 100))
    doc.h1("Invitasjon til hagefest")
    doc.p("Kjære naboer,")
    doc.p(
        f"Lørdag {doc.num(fake.fd(d0))} inviterer vi alle i gata til hagefest hos oss i {addr}. "
        f"Vi tenner grillen klokken {doc.num('15:00')}, og barna kan se frem til sekkeløp, ansiktsmaling og is."
    )
    doc.p(
        f"For å dekke mat og drikke foreslår vi et bidrag på kr {doc.num(a1)} per husstand. "
        f"Vi har allerede kjøpt inn telt og bord for kr {doc.num(a2)}, så det er bare å møte opp med godt humør."
    )
    doc.p(
        f"Gi gjerne beskjed innen {doc.num(fake.fd(d0 - timedelta(days=7)))} om dere kommer, og om noen har "
        "allergier vi bør ta hensyn til. Ta med egne stoler hvis dere har."
    )
    return None, [a1, a2]


def _club(doc: Doc, r: random.Random, me: str, addr: str, when: date) -> tuple[str, list[str]]:
    rec = r.choice(CLUBS)
    d0 = when + timedelta(days=r.randint(14, 60))
    a1 = fake.nok(fake.money(r, 600, 2400, 5000))
    a2 = fake.nok(fake.money(r, 200, 900, 5000))
    kids = r.randint(1, 3)
    doc.h1("Spørsmål om medlemskap")
    doc.p("Hei,")
    doc.p(
        f"Vi har nylig flyttet til området og ønsker å melde inn {doc.num(str(kids))} barn i {rec}. "
        f"På nettsiden står det at årskontingenten er kr {doc.num(a1)}, og at treningsavgiften kommer i tillegg."
    )
    doc.p(
        f"Kan dere bekrefte om søskenmoderasjonen på kr {doc.num(a2)} fortsatt gjelder, og om det er mulig å "
        f"starte før sesongstart {doc.num(fake.fd(d0))}? {'Barnet' if kids == 1 else 'Barna'} har "
        "spilt i klubb tidligere og gleder seg til å komme i gang."
    )
    doc.p("Jeg kan også stille som dugnadsforelder eller hjelpe til med kjøring til kamper i helgene.")
    return rec, [a1, a2]


VARIANTS = [_landlord, _neighbour, _club]


def build(r: random.Random) -> Doc:
    me, addr, pc_city, city = fake.person(r)
    when_d = fake.rdate(r, span=280)
    when = fake.fd(when_d)
    tel = fake.phone(r)
    variant = r.choice(VARIANTS)
    doc = Doc("letter", css=".page{font-size:11pt;line-height:1.5} .page p{margin:0 0 3.5mm} h1{margin-top:8mm}")
    doc.lines([me, addr, pc_city])
    rec_lines: list[str] = []
    if variant is not _neighbour:
        rec_pc, rec_city = fake.city(r)
        rec_lines = [fake.street(r), f"{rec_pc} {rec_city}"]
    # The recipient name is only known after the variant has picked it, so build the body
    # in a scratch doc and splice it in after the address/date blocks.
    body = Doc("letter")
    rec, amounts = variant(body, r, me, addr, when_d)
    if rec:
        doc.raw('<div style="height:10mm"></div>')
        doc.lines([rec, *rec_lines])
        doc.field("recipient", rec)
    doc.raw('<div class="right" style="margin-top:8mm">')
    doc.lines([f"{city}, {doc.num(when)}"])
    doc.raw("</div>")
    pg = body.pages[0]
    doc.cur.html.extend(pg.html)
    doc.cur.gt.extend(pg.gt)
    for n in pg.numbers:
        doc.num(n)
    doc.p("Med vennlig hilsen")
    doc.sig(me)
    doc.lines([me])
    doc.lines([f"Telefon: {doc.num(tel)}"], cls="small")
    doc.title = body.pages[0].gt[0]
    doc.field("sender_name", me)
    doc.field("date", when)
    doc.field("amounts", amounts)
    doc.field("phone", tel)
    return doc
