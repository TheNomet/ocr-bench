"""Two-page residential tenancy agreement (husleiekontrakt) with numbered clauses."""

from __future__ import annotations

import random
from datetime import date, timedelta

from .. import fake
from ..builder import Doc

SCHEMA = {
    "description": "Residential tenancy agreement between a landlord and a tenant (2 pages)",
    "fields": {
        "landlord": {"type": "string", "description": "Name of the landlord (utleier)"},
        "tenant": {"type": "string", "description": "Name of the tenant (leietaker)"},
        "property_address": {"type": "string", "description": "Address of the rented home, as printed"},
        "start_date": {"type": "string", "description": "Start date of the tenancy (dd.mm.yyyy)"},
        "monthly_rent": {"type": "string", "description": "Monthly rent in NOK"},
        "deposit": {"type": "string", "description": "Deposit amount in NOK"},
        "notice_period": {"type": "string", "description": "Notice period, as printed (e.g. '3 måneder')"},
    },
}

LANDLORD_COS = ["Bergheim Eiendom AS", "Lyngstad Boliger AS", "Utleie Vest AS"]
ROOMS = [  # (minimum size in m², description)
    (28, "kombinert stue og kjøkken, bad og ett soverom"),
    (40, "stue, kjøkken, bad og ett soverom"),
    (55, "stue med åpen kjøkkenløsning, bad og to soverom"),
    (75, "stue, kjøkken, bad, vaskerom og tre soverom"),
]


def build(r: random.Random) -> Doc:
    landlord = r.choice(LANDLORD_COS) if r.random() < 0.35 else fake.name(r)
    l_street, l_pc_city = fake.street(r), " ".join(fake.city(r))
    tenant, t_old_street, t_old_pc_city, _ = fake.person(r)
    pc, city = fake.city(r)
    flat = f"{fake.street(r)}, {pc} {city}"
    unit = f"H{r.randint(1, 5)}0{r.randint(1, 4)}"
    size_m2 = r.randint(28, 110)
    size = str(size_m2)
    rooms = [desc for lo, desc in ROOMS if size_m2 >= lo][-1]
    start = date(2026, r.randint(1, 12), 1)
    rent_c = fake.money(r, 8000, 22000, 50000)
    rent = fake.nok(rent_c)
    deposit = fake.nok(rent_c * r.choice([2, 3, 3]))
    pay_day = str(r.choice([1, 1, 5, 20]))
    months = r.choice([1, 2, 3, 3])
    notice = "1 måned" if months == 1 else f"{months} måneder"
    acct = fake.account(r)
    contract_no = str(r.randint(1000, 9999))
    signed = fake.fd(start - timedelta(days=r.randint(7, 40)))
    s_city = l_pc_city.split(" ", 1)[1]

    css = (
        ".page{font-size:9.8pt;line-height:1.42;text-align:justify}"
        ".page h1{text-align:center;font-size:18pt;margin-bottom:1mm}"
        ".page h2{font-size:11pt;margin:4mm 0 1.2mm}"
        ".page p{margin:0 0 1.8mm}"
        ".sub{text-align:center;color:#444;margin-bottom:5mm !important}"
        ".pageno{text-align:center !important;font-size:8pt;color:#555;margin-top:6mm !important}"
        ".parties .row{justify-content:flex-start}.parties .row span:first-child{width:38mm;color:#333}"
    )
    doc = Doc("rental_contract", title="Husleiekontrakt", css=css)

    # ---- page 1 -------------------------------------------------------------
    doc.h1("Husleiekontrakt")
    doc.p(f"for leie av bolig – kontrakt nr. {doc.num(contract_no)}", cls="sub")
    doc.h2("§ 1 Partene")
    doc.raw('<div class="box parties">')
    doc.kv("Utleier", landlord, cls="")
    doc.kv("Adresse", f"{l_street}, {l_pc_city}", cls="")
    doc.kv("Leietaker", tenant, cls="")
    doc.kv("Nåværende adresse", f"{t_old_street}, {t_old_pc_city}", cls="")
    doc.kv("Telefon leietaker", doc.num(fake.phone(r)), cls="")
    doc.raw("</div>")
    doc.h2("§ 2 Leieobjektet")
    doc.p(
        f"Kontrakten gjelder leie av boligen i {flat}, leilighet {unit}. Boligen er på ca. {doc.num(size)} m² "
        f"og består av {rooms}. I tillegg følger det med en kjellerbod og rett til bruk av "
        "fellesarealer som sykkelbod, tørkerom og uteområder etter de til enhver tid gjeldende ordensreglene."
    )
    doc.p(
        "Boligen leies ut møblert med hvitevarer som komfyr, kjøleskap og oppvaskmaskin. En egen liste over "
        "inventar og boligens tilstand ved innflytting skal fylles ut og signeres av begge parter."
    )
    doc.h2("§ 3 Leietid")
    doc.p(
        f"Leieforholdet starter {doc.num(fake.fd(start))} og løper på ubestemt tid inntil det blir sagt opp "
        "av en av partene i samsvar med § 6. Nøkler utleveres på overtakelsesdagen etter avtale, og "
        "leietaker kvitterer for antall nøkler ved mottak."
    )
    doc.h2("§ 4 Husleie")
    doc.p(
        f"Månedlig husleie er kr {doc.num(rent)}. Leien betales forskuddsvis innen den {doc.num(pay_day)}. "
        f"hver måned til utleiers konto {doc.num(acct)}. Strøm, internett og eventuelle andre "
        "abonnementer betales av leietaker direkte til leverandør og er ikke inkludert i leien."
    )
    doc.p(
        "Leien kan justeres én gang per år i tråd med endringen i konsumprisindeksen, med minst én måneds "
        "skriftlig varsel. Ved forsinket betaling kan utleier kreve forsinkelsesrente og purregebyr etter "
        "gjeldende regler."
    )
    doc.h2("§ 5 Depositum")
    doc.p(
        f"Leietaker stiller et depositum på kr {doc.num(deposit)}. Beløpet settes inn på en egen "
        "depositumskonto i leietakers navn innen innflytting. Depositumet kan bare utbetales etter skriftlig "
        "avtale mellom partene, eller når det foreligger rettskraftig dom eller avgjørelse om dette."
    )
    doc.h2("§ 6 Oppsigelse")
    doc.p(
        f"Gjensidig oppsigelsestid er {notice}, regnet fra den første dagen i måneden etter at oppsigelsen "
        "ble mottatt. Oppsigelsen skal være skriftlig. Oppsigelse fra utleier må begrunnes og opplyse om at "
        "leietaker kan protestere skriftlig innen fristen som følger av husleieloven."
    )
    doc.p(
        "Ved oppsigelse plikter leietaker å gi utleier og interesserte leietakere adgang til boligen for "
        "visning på rimelige tidspunkter, etter avtale og med minst ett døgns varsel."
    )
    doc.p(f"Side {doc.num('1')} av {doc.num('2')}", cls="pageno")

    # ---- page 2 -------------------------------------------------------------
    doc.new_page()
    doc.h2("§ 7 Vedlikehold og bruk")
    doc.p(
        "Leietaker skal behandle boligen med tilbørlig aktsomhet og sørge for vanlig renhold, utskifting av "
        "lyspærer og sikringer, rensing av sluk og vannlåser samt testing av røykvarslere. Skader som oppstår "
        "skal meldes til utleier uten ugrunnet opphold, slik at større følgeskader kan unngås."
    )
    doc.p(
        "Utleier holder boligen i forsvarlig stand og sørger for vedlikehold av tak, vegger, vinduer, "
        "varmtvannsbereder og faste installasjoner. Utleier skal varsle i rimelig tid før arbeid i boligen, "
        "med mindre det haster for å avverge skade."
    )
    doc.h2("§ 8 Husdyr og røyking")
    doc.p(
        "Det er ikke tillatt å røyke innendørs. Husdyr kan bare holdes etter skriftlig samtykke fra "
        "utleier, og leietaker er ansvarlig for skader og ekstra rengjøring som skyldes dyreholdet."
    )
    doc.h2("§ 9 Fremleie")
    doc.p(
        "Leietaker kan ikke fremleie hele eller deler av boligen uten utleiers skriftlige samtykke. "
        "Leietakers ektefelle, samboer og nærmeste familie kan likevel bo i boligen sammen med leietaker."
    )
    doc.h2("§ 10 Tilbakelevering")
    doc.p(
        "Ved leieforholdets slutt skal boligen tilbakeleveres ryddet, rengjort og i samme stand som ved "
        "overtakelsen, bortsett fra vanlig slitasje. Alle nøkler skal leveres tilbake, også kopier som "
        "leietaker selv har fått laget. Partene gjennomgår boligen sammen før endelig oppgjør."
    )
    doc.h2("§ 11 Øvrige bestemmelser")
    doc.p(
        "Ordensreglene for eiendommen er vedlagt og gjelder som en del av kontrakten. For øvrig gjelder "
        "husleieloven. Kontrakten er utstedt i to eksemplarer, ett til hver av partene."
    )
    doc.h2("§ 12 Forsikring")
    doc.p(
        "Utleier holder bygningen forsikret. Leietaker er selv ansvarlig for å forsikre eget innbo og løsøre, "
        "og oppfordres til å ha en innboforsikring som også dekker ansvar overfor utleier."
    )
    doc.h2("§ 13 Tvister")
    doc.p(
        "Uenighet om forståelsen av kontrakten skal partene først forsøke å løse ved forhandlinger. Fører ikke "
        "dette frem, kan hver av partene bringe saken inn for Husleietvistutvalget eller de alminnelige "
        "domstolene etter reglene i husleieloven."
    )

    doc.raw('<div class="flex" style="margin-top:14mm"><div>')
    doc.lines([f"Sted og dato: {s_city}, {doc.num(signed)}"])
    doc.sig(landlord if not landlord.endswith(" AS") else fake.name(r))
    doc.lines(["Utleier"], cls="sigline")
    doc.lines([landlord], cls="small")
    doc.raw("</div><div>")
    doc.lines([f"Sted og dato: {s_city}, {doc.num(signed)}"])
    doc.sig(tenant)
    doc.lines(["Leietaker"], cls="sigline")
    doc.lines([tenant], cls="small")
    doc.raw("</div></div>")
    doc.p(f"Side {doc.num('2')} av {doc.num('2')}", cls="pageno")

    doc.field("landlord", landlord)
    doc.field("tenant", tenant)
    doc.field("property_address", flat)
    doc.field("start_date", fake.fd(start))
    doc.field("monthly_rent", rent)
    doc.field("deposit", deposit)
    doc.field("notice_period", notice)
    return doc
