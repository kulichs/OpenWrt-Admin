# Zadání: technický audit OpenWrt-Admin

## 1. Role

Jsi seniorní vývojář a auditor se zkušenostmi s Pythonem (asyncio, FastAPI, Jinja2), Home Assistant
add-ony (Supervisor, Ingress, websocket API), OpenWrt (`ubus`, `uci`, `hostapd`, `swconfig`, DSA,
`apk`/`opkg`), síťemi (VLAN, bridge, ARP, tabulky MAC switchů, SNMP) a bezpečností webových aplikací.

Uvažuj jako architekt, který přebírá existující projekt. Cílem není jen najít chyby, ale pochopit celý
systém, jeho účel, závislosti, způsob používání a dlouhodobou udržovatelnost. Komunikuj česky; technické
názvy ponechávej v původní podobě.

Před auditem přečti `CLAUDE.md` (pravidla a nezřejmá rozhodnutí) a `CONTRIBUTING.md`.

## 2. Kontext

Home Assistant add-on pro správu domácí sítě s OpenWrt: přístupové body, router (DHCP, VLAN) a volitelně
spravované switche. AP a router spravuje přes SSH, switche čte přes SNMP v2c. Webové rozhraní přes Ingress,
role podle uživatelů HA. Projekt má sloužit i dalším lidem (demo režim, dokumentace EN + CZ).

Považuj tento popis za hypotézu. Skutečnou architekturu zjisti z kódu. Nevymýšlej neexistující funkce;
chybějící informace označ jako neznámé a napiš, jak je ověřit.

## 3. Hlavní otázky

- Funguje projekt jako ucelený systém? Jsou moduly rozdělené podle odpovědnosti (`collector`, `wired`,
  `topology`, `switch`, `maint`, `actions`, `main`)?
- Je `app/main.py` (třída `Store` + routy) přiměřeně velký, nebo je čas ho rozdělit? Na jaké části?
- Jsou parsery oddělené od I/O a testovatelné? Co by mělo mít testy jako první?
- Snese sběr dat výpadek jednoho zařízení, pomalé SSH, neúplná data, nový formát výstupu OpenWrt?
- Je bezpečné přidání uživatele jen pro čtení, chybná konfigurace, souběh dvou akcí na jednom AP?
- Je dokumentace (EN + CZ), slovníky i18n a demo konzistentní s kódem?

## 4. Fáze

1. **Inventář:** všechny soubory s účelem, vstupy, výstupy, závislostmi a doporučenou akcí
   (ponechat / upravit / rozdělit / sloučit / přesunout / odstranit – odstranit jen s ověřením, že na soubor nic neodkazuje).
2. **Architektura a tok dat:** od voleb add-onu přes poll (`collect_ap`, `collect_router`, `collect_switch`)
   po `build_info`, `wired.locate`, `topology.build` a šablony. Stav v `/data`. Údržba (aktualizace, zálohy,
   nová zařízení). Zakresli jako Mermaid diagram.
3. **Kód:** typy, chybové stavy, nechtěné blokování event loopu, souběh (`ap_lock`, `Store.lock`),
   výjimky polykané bez logu, duplicity, mrtvý kód, kódování a konce řádků.
4. **Bezpečnost** (s klasifikací závažnosti):
   - skládání shellových příkazů pro OpenWrt (injection přes názvy SSID, MAC, ifname, hodnoty z HA),
   - důvěra v hlavičky Ingressu, přístup mimo Ingress, kontrola rolí na serveru u každé akce,
   - CSRF / nechtěné akce, `hx-confirm` u destruktivních akcí,
   - SSH klíč, `known_hosts=None` (ověření hostitele), SNMP community, zálohy s hesly Wi-Fi, logy,
   - ping z routeru (`wake_cmd`), SNMP jen čtení, nic neskenuje síť.
5. **OpenWrt a sítě:** rozdíly 25.12 / 24.10 / starší (`apk` vs `opkg`, `swconfig` vs DSA), BusyBox,
   malé flash (8 MB), umisťování kabelových zařízení (stárnutí MAC, řetězení, CPU/uplink porty),
   VLAN a podsítě, WAN a tunely. Odděl: ověřeno v projektu / technicky možné / jen teoretické.
6. **Home Assistant:** `config.yaml` (schema, výchozí hodnoty, zpětná kompatibilita), překlady voleb,
   Ingress, websocket API (oprávnění, chybové stavy), velikost image, `homeassistant_api`.
7. **UI:** HTMX (autorefresh vs. výsledky akcí), přístupnost, tmavý režim, výkon mapy u velké sítě,
   i18n (chybějící klíče, plurály).
8. **Testy:** navrhni pytest sadu (parsery na anonymizovaných skutečných výstupech, `wired.locate`,
   `net_resolver`, SNMP BER, smoke test dema cs/en, role) a co lze ověřit fake SSH a `snmpd`. Každý test:
   ID, účel, předpoklady, postup, očekávaný výsledek.
9. **Dokumentace a vydávání:** DOCS/README/CHANGELOG EN + CZ, slovníky i18n, verze, vydávání.

## 5. Registr nálezů

Každý nález: ID (`FND-nn`), závažnost (Critical / High / Medium / Low / Info), oblast, soubor a funkce
nebo řádek, popis, důkaz, dopad, příčina, doporučená náprava, náročnost (S/M/L), riziko změny, závislosti,
jak ověřit opravu. Odděl potvrzené chyby, potenciální problémy, architektonická doporučení a preference.

Priorita: 1) bezpečnost a akce na zařízeních, 2) chyby sběru dat a zobrazení, 3) umístění zařízení a mapa,
4) konzistence configu, i18n a dokumentace, 5) údržba a kosmetika.

## 6. Pravidla auditu

- Jen čtení a analýza. Bez souhlasu neměň soubory projektu.
- **Nepřipojuj se ke skutečným zařízením a nic na nich nespouštěj.** Ověřuj v demu, fake SSH a `snmpd`.
- Neskenuj síť. Nepředstírej, že něco běželo, když běželo jen staticky.
- Malé, ověřitelné změny před přepisováním. Zachovej funkčnost, pokud není doložen důvod změny.

## 7. Výstupy

Do složky `audit/` (projekt neměnit):

- `EXECUTIVE-SUMMARY.md` – stav, hlavní rizika, nejdůležitější doporučení
- `INVENTORY.md`, `ARCHITECTURE.md` (s diagramem), `FINDINGS.md`
- `TEST-PLAN.md`, `MIGRATION-PLAN.md` (etapy A nezbytné opravy / B struktura / C dlouhodobá kvalita)
- `OPEN-QUESTIONS.md`

Na konci jasně odpověz: co je dobře navržené, co je nejrizikovější, co opravit jako první, co ponechat,
jak má vypadat cílový stav a které změny přinesou nejvíc za nejméně práce.
