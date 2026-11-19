# Vodič za intervju: SEF AI Agent

Ovaj dokument je za tebe, da projekat možeš da objasniš i braniš na intervjuu. Pročitaj ga jednom celog, pa uradi
vežbe na kraju. Posle toga ćeš znati odgovor na skoro svako pitanje.

## 1. Priča za 30 sekundi

"U Srbiji od 2023. sve B2B fakture stižu kroz SEF kao UBL XML. Firme i dalje ručno upoređuju svaku fakturu sa
narudžbenicom i prijemnicom, traže duplikate, biraju konto i odgovaraju na pitanja tipa koliko dugujemo dobavljaču.
Napravio sam AI agenta koji to radi: povlači fakture iz SEF-a, radi 3-way match, hvata duplikate i sumnjive
promene tekućeg računa, predlaže knjiženje i odgovara na pitanja na srpskom. Čovek odobrava svaku akciju, a sve
ide u audit log koji se ne može neprimetno menjati. Na test skupu od 162 fakture sve ubačene greške su uhvaćene,
a procena uštede je oko 50 sati mesečno za firmu sa 400 faktura."

## 2. Kako da opišeš svoju ulogu (iskreno)

"Ja sam vlasnik projekta: izabrao sam problem i obim, postavio ciljeve, prihvatio arhitektonske odluke i vodio
izradu. Kod je pisan uz Claude kao AI buildera, što je i poenta: pokazujem kako se AI agent orkestrira da napravi
ozbiljan sistem, a ja razumem i branim svaku odluku."

Ne govori da si sve sam kucao. Ako pitaju, reci ovo gore. Za AI pozicije je to prednost, ako znaš da objasniš
zašto je sistem ovako napravljen. Odluke su zapisane u `DECISIONS.md`, nauči D2, D4, D5, D6 i D7.

## 3. Put jedne fakture kroz sistem

1. **Preuzimanje.** `SefClient.list_incoming_ids` vraća ID-jeve novih ulaznih faktura, `get_invoice_xml` vraća UBL
   XML. U demo režimu isti interfejs čita fajlove iz foldera (`MockSefClient`).
2. **Parsiranje.** `sef/ubl.py` čita broj, datume, PIB dobavljača, broj narudžbenice (`OrderReference`), tekući
   račun, stavke i PDV. Parser ima isključene eksterne entitete (zaštita od XXE napada).
3. **Kontrole** (`matching/engine.py`), redom:
   - tačan duplikat: isti PIB i isti broj fakture posle normalizacije (`ABC-2026-0018` = `ABC/2026/18`);
   - mogući duplikat: isti iznos kod istog dobavljača u roku od 45 dana;
   - PIB: kontrolna cifra po ISO 7064 MOD 11,10;
   - nepoznat dobavljač i **promenjen tekući račun** (klasična prevara: "promenili smo banku, platite ovde");
   - PDV i zbir;
   - 3-way match: cena prema narudžbenici (tolerancija 1%), količina prema prijemnici umanjena za ono što je već
     fakturisano, stavke kojih nema na narudžbenici.
4. **Status.** Ako postoji ijedan blokirajući nalaz, faktura je EXCEPTION. Inače READY.
5. **Predlog knjiženja** (`booking/`). Stavka sa šifrom artikla uzima konto iz šifarnika. Stavka sa slobodnim
   tekstom (usluge, struja, zakup) ide u LLM klasifikator koji vraća konto, sigurnost i obrazloženje kao
   strukturisan JSON. Knjiženje: duguje trošak ili zalihe (osnovica) i PDV (2700/2710), potražuje dobavljač (4350).
6. **Zahtev za odobrenje.** Agent pravi `ActionRequest`: "proknjiži i prihvati u SEF-u" ili "odbij u SEF-u" (za
   duplikate i nepoznate dobavljače). Ništa se ne izvršava dok imenovana osoba ne klikne Odobri.
7. **Audit.** Svaki korak (primljeno, provereno, predloženo, odobreno, poslato u SEF) upisuje se u log gde svaki
   red čuva heš prethodnog.

## 4. Chat agent

- Pitanje ide modelu zajedno sa listom alata: `payables`, `spend_by_supplier`, `list_exceptions`, `get_invoice`,
  `run_sql` i `request_invoice_rejection`.
- Model bira alat, kod ga izvršava nad bazom, rezultat se vraća modelu, i tako u petlji dok model ne da konačan
  odgovor (najviše 8 koraka). To je "tool use" ili "function calling".
- Pravilo: **model ne računa**. Svaki broj mora doći iz alata. Zato se odgovori mogu proveriti, i evaluacija ih
  poredi sa SQL rezultatom.
- `run_sql` dozvoljava samo jedan SELECT, blokira ključne reči za izmenu i izvršava se u savepointu koji se vraća.
- Agent može samo da *zatraži* odbijanje fakture. Zahtev čeka čoveka.

## 5. Brojevi koje treba da znaš

- 162 sintetičke fakture, 110 čistih, ostale sa ubačenim problemima (11 tipova).
- Tačno rutiranje 98,8%: sve greške uhvaćene, 2 lažna "moguća duplikata" (dve legitimne fakture sa istim iznosom).
  To je namerno: minut provere je jeftiniji od duplog plaćanja.
- Knjiženje: pravila sa ključnim rečima su 98,8% na generisanim podacima ali samo 43,6% na ručno napisanom
  "teškom skupu". Tu LLM treba da pokaže vrednost (broj se dobija kad se pokrene eval sa API ključem).
- ROI: 400 faktura mesečno, 12 minuta ručno po fakturi, oko 51 sat i oko 60.000 RSD uštede mesečno. Sve
  pretpostavke su u `roi.py` i menjaju se klizačima u dashboardu.

Budi iskren o ograničenju: generator podataka i pravila je pisao isti tim, pa detekcija dokazuje da kontrole rade
kako je specificirano, a ne tačnost na pravim podacima. Pravi broj daje pilot na SEF podacima jedne firme.

## 6. Pitanja i odgovori

**Zašto ne pustiš LLM da sam proceni da li je faktura ispravna?**
Zato što je to aritmetika i pravila, a odluke o novcu moraju biti ponovljive i objašnjive revizoru. LLM koristim
tamo gde pravila ne rade dobro: slobodan tekst i pitanja na prirodnom jeziku.

**Zašto čovek odobrava sve, zar to ne ubija automatizaciju?**
Većina posla je priprema: uparivanje, provera, konto. Kada je to gotovo, odobravanje je jedan klik. Prihvatanje
fakture u SEF-u ima poreske posledice (pravo na odbitak PDV-a), pa firma na početku neće dati agentu da to radi
sam. Kad se na pravim podacima pokaže tačnost, čiste fakture mogu da idu automatski, to je promena konfiguracije.

**Kako sprečavaš da model izmisli broj?**
Sistemski prompt zabranjuje brojeve koji nisu iz alata, svi iznosi dolaze iz SQL-a, a evaluacija poredi odgovor
sa SQL rezultatom. Ako alat ne vrati podatak, agent kaže da ga nema.

**Šta ako neko pokuša prompt injection, npr. tekst u opisu stavke "odobri ovu fakturu"?**
Agent nema alat koji odobrava ili plaća. Najviše može da zatraži odbijanje, a i to čeka čoveka. Bezbednost ne
zavisi od toga da li je model poslušao prompt, nego od toga koje alate uopšte ima.

**Zašto dva modela?**
Klasifikacija stavke je uzak zadatak sa velikim brojem poziva, pa ide na mali i jeftin model sa strukturisanim
izlazom. Razgovor zahteva više koraka i alate, pa ide na jači model. Oba se menjaju preko env varijabli, a eval
meri tačnost i cenu po stavci da odluka bude na brojevima.

**Koliko košta po fakturi?**
Pravila ne koštaju ništa. LLM se poziva samo za stavke bez šifre artikla. Eval meri tačnu cenu po stavci iz
`usage` polja odgovora (ulazni, izlazni i keširani tokeni), i to ulazi u ROI.

**Kako radi detekcija duplikata?**
Dva nivoa. Tačan: isti PIB i isti broj posle normalizacije (slova velika, brojevi bez vodećih nula, separatori
nebitni). Mogući: isti iznos kod istog dobavljača u 45 dana. Tačan duplikat se odmah predlaže za odbijanje.

**Zašto je promenjen tekući račun blokirajući?**
To je najčešća prevara u nabavci: lažni mejl "promenili smo banku". Sistem poredi račun sa fakture sa matičnim
podacima dobavljača i traži proveru telefonom pre plaćanja.

**Kako radi 3-way match za delimične isporuke?**
Za svaku stavku računa se primljena količina iz prijemnica minus ono što su ranije fakture za istu narudžbenicu
već fakturisale. Ako nova faktura traži više od ostatka, to je nalaz QTY_NOT_RECEIVED.

**Šta je hash-chain audit log?**
Svaki red loga ima SHA-256 heš svog sadržaja i heša prethodnog reda. Ako neko izmeni ili obriše red, lanac puca od
tog mesta, i `verify-audit` pokaže gde. Test u projektu to dokazuje.

**Kako bi ovo pustio u pravu firmu?**
1) SEF API ključ za demo okruženje i provera endpointa. 2) Uvoz matičnih podataka, narudžbenica i prijemnica iz
njihovog ERP-a. 3) Pilot u "shadow" režimu dve nedelje: agent predlaže, ljudi rade kao i pre, poredimo. 4) SSO i
uloge (ko sme da odobri koji iznos). 5) Izvoz knjiženja u njihov ERP.

**Zašto SQLite i PostgreSQL?**
SQLite za lokalni demo i testove bez instalacije, PostgreSQL za produkciju (docker-compose). Kod je kroz
SQLAlchemy isti za oba, i ceo tok je pokrenut na oba.

**Šta nije urađeno?**
Pravi login i uloge, izvoz u konkretan ERP, provera kontnog plana sa sertifikovanim računovođom, i pilot na pravim
podacima. Ovo reci sam, pre nego što pitaju.

**Zašto si napravio mock SEF?**
SEF demo traži nalog firme i API ključ. Mock ima isti interfejs (`SefClient`), pa sve radi i testira se bez
pristupa, a prelazak je jedna env varijabla.

**Šta bi radio sledeće?**
Pilot na pravim podacima, učenje iz ispravki (kada knjigovođa promeni konto, to postaje primer za klasifikator),
i automatsko prihvatanje čistih faktura ispod određenog iznosa kad tačnost bude dokazana.

## 7. Vežbe (uradi ih pre intervjua)

1. Pokreni projekat lokalno po README-u (`sef-agent seed`, `sef-agent serve`) i prođi kroz dashboard: otvori jedan
   izuzetak, odobri jedan predlog, pogledaj audit log.
2. Promeni toleranciju cene na 0,3%: `PRICE_TOLERANCE_PCT=0.3 sef-agent seed --rules-only`, pa `sef-agent eval`.
   Pogledaj kako se menja broj izuzetaka i objasni zašto.
3. Dodaj jedno pravilo sam: npr. faktura sa dospećem kraćim od 5 dana od nepoznatog dobavljača dobija napomenu.
   Mesto je `check_invoice` u `matching/engine.py`, a ozbiljnost u tabeli `SEVERITY`.
4. Ručno izmeni jedan red u tabeli `audit_log` (npr. preko `sqlite3 data/sef_agent.db`) i pokreni
   `sef-agent verify-audit`.
5. Kad dobiješ API ključ: `sef-agent ask "Koliko dugujemo dobavljaču Agro Lux do kraja meseca?"` i pogledaj koje je
   alate agent pozvao.

## 8. Rečnik

- **SEF**: Sistem elektronskih faktura Ministarstva finansija; obavezan za B2B od 2023.
- **UBL 2.1**: XML standard za fakture; SEF koristi evropski EN 16931 sa srpskim dodacima (SRBDT).
- **3-way match**: poređenje fakture, narudžbenice i prijemnice.
- **PIB / MB**: poreski identifikacioni broj / matični broj.
- **Konto**: račun u kontnom planu (npr. 5330 zakupnine, 2700 PDV, 4350 dobavljači u zemlji).
- **Tool use**: model ne odgovara direktno nego traži da se pozove funkcija, pa nastavlja sa rezultatom.
- **Strukturisan izlaz**: model mora da vrati JSON po šemi (ovde konto, sigurnost, obrazloženje).
- **Human-in-the-loop**: čovek odobrava akcije koje agent predlaže.
- **Evaluacija**: merenje tačnosti na skupu sa poznatim tačnim odgovorima, uz cenu i vreme.
