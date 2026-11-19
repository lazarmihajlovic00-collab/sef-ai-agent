"""Simplified excerpt of the Serbian chart of accounts (kontni okvir za privredna drustva)
used for purchase bookings. Four-digit analytical accounts."""

ACCOUNTS: dict[str, str] = {
    "1010": "Materijal u magacinu",
    "1320": "Roba u magacinu",
    "5120": "Troskovi ostalog (rezijskog) materijala",
    "5130": "Troskovi goriva i energije",
    "5310": "Troskovi transportnih usluga",
    "5311": "Troskovi PTT i telekomunikacionih usluga",
    "5320": "Troskovi usluga odrzavanja",
    "5330": "Troskovi zakupnina",
    "5350": "Troskovi reklame i propagande",
    "5500": "Troskovi neproizvodnih usluga",
    "2700": "PDV u primljenim fakturama po opstoj stopi (20%)",
    "2710": "PDV u primljenim fakturama po posebnoj stopi (10%)",
    "4350": "Dobavljaci u zemlji",
}

VAT_ACCOUNT = {20: "2700", 10: "2710"}
PAYABLES_ACCOUNT = "4350"

# Short guidance given to the LLM classifier (and shown in the UI)
ACCOUNT_GUIDE = """\
1010 Materijal u magacinu - sirovine i materijal za proizvodnju
1320 Roba u magacinu - roba nabavljena radi dalje prodaje
5120 Troskovi rezijskog materijala - kancelarijski materijal, sredstva za higijenu, sitan inventar
5130 Troskovi goriva i energije - gorivo, struja, gas, grejanje
5310 Troskovi transportnih usluga - prevoz, spedicija, kurirske usluge
5311 Troskovi PTT i telekomunikacionih usluga - telefon, internet, posta
5320 Troskovi usluga odrzavanja - servis, popravke, odrzavanje opreme i objekata
5330 Troskovi zakupnina - zakup poslovnog prostora, opreme, vozila
5350 Troskovi reklame i propagande - oglasavanje, marketing, stampa promotivnog materijala
5500 Troskovi neproizvodnih usluga - IT usluge, softverske licence, racunovodstvo, konsalting, obezbedjenje
"""
EXPENSE_ACCOUNTS = ["1010", "1320", "5120", "5130", "5310", "5311", "5320", "5330", "5350", "5500"]
