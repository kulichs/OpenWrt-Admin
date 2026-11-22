# CLAUDE.md – pravidla projektu OpenWrt-Admin

Pokyny pro každého, kdo upravuje tento repozitář: lidi i AI agenty (Claude, Codex, Copilot, …).
Pravidla se mění **jen v tomto souboru**.

Související: [`CONTRIBUTING.md`](CONTRIBUTING.md) (jak začít, spustit demo, testovat, vydat verzi),
[`openwrt_admin/DOCS.md`](openwrt_admin/DOCS.md) (uživatelská dokumentace EN + CZ),
[`openwrt_admin/CHANGELOG.md`](openwrt_admin/CHANGELOG.md) (změny po verzích EN + CZ),
[`docs/audit-prompt.md`](docs/audit-prompt.md) (zadání pro technický audit).

## Role

Pracuj jako seniorní vývojář v Pythonu se zkušenostmi s:

- OpenWrt (25.12, 24.10, starší s `opkg`): `ubus`, `uci`, `hostapd`, `swconfig` i DSA, BusyBox `ash`,
- Home Assistant add-ony (Supervisor, Ingress, `config.yaml`, překlady voleb, websocket API),
- asyncio, FastAPI, Jinja2, HTMX, `asyncssh`,
- sítěmi: VLAN, bridge, ARP, tabulky MAC adres switchů, SNMP (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB).

Komunikuj česky. Názvy příkazů, funkcí, proměnných a technické termíny ponechávej v původní (anglické) podobě.

## Co to je

Home Assistant add-on pro centrální správu domácí sítě s OpenWrt: přístupové body, router a volitelně
spravované switche. Webové rozhraní běží přes Ingress v panelu Home Assistantu.

- **AP a router** se spravují přes **SSH** (klíč add-onu v `/data`). Čte se jen to, co OpenWrt nabízí
  (`ubus`, `uci`, `swconfig`, `/proc/net/arp`), **na zařízeních se nic neinstaluje**.
- **Switche** se čtou přes **SNMP v2c, jen pro čtení** (vlastní klient `app/snmp.py`, bez knihovny).
- **Akce** (restart, SSID, `apk`, zálohy, odpojení klienta) jen pro uživatele s plným přístupem.
- Uživatelé a role podle hlaviček Ingressu (`X-Remote-User-*`) a voleb `full_access` / `read_access`.

Repozitář: [GitHub `kulichs/OpenWrt-Admin`](https://github.com/kulichs/OpenWrt-Admin) (zrcadlo hlavního repozitáře správce).
Článek o projektu: [tadyk.info](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).
Home Assistant si add-on stahuje z repozitáře (`ha store reload` → Aktualizovat).

## Cílová platforma

- **Add-on:** `python:3.12-alpine`, závislosti připnuté v `requirements.txt`. Novou závislost přidávej jen
  s důvodem (malý image, žádné nativní kompilace); jednoduché věci napiš sám (SNMP klient je vzor).
- **OpenWrt:** příkazy musí fungovat v BusyBox `ash` (žádný bash, žádné GNU přepínače). Počítej s `apk`
  (25.12+) i `opkg` (starší), se `swconfig` i DSA, se zařízeními s 8 MB flash (bez LuCI).
- **Home Assistant:** volby add-onu = `config.yaml` (`options` + `schema`) + `translations/{en,cs}.yaml`
  + výchozí hodnota v `load_options()` v `app/main.py`. Starý config bez nového klíče musí fungovat.

## Pravidla práce

1. **Změna = ověření + dokumentace.** Každá změna chování se před předáním ověří (demo režim, testy,
   simulace – viz CONTRIBUTING) a zdokumentuje. Nepředstírej, že kód běžel na skutečném zařízení, když běžel
   jen proti demu nebo simulaci. Rozlišuj: *statická kontrola* / *demo* / *simulace (fake SSH, `snmpd`)* /
   *ověřeno na skutečné síti*.
2. **Dokumentace drží krok s kódem:** `DOCS.md` (EN + CZ), `README.md` (EN + CZ), `CHANGELOG.md` (EN + CZ),
   `translations/*.yaml` a slovníky `app/i18n/{cs,en}.json` (oba se stejnými klíči). Nová volba, funkce
   nebo změna chování se promítne do všech, které se jí týkají.
3. **Verze:** každé vydání zvedne `version` v `openwrt_admin/config.yaml` (SemVer: PATCH = oprava,
   MINOR = nová funkce, MAJOR = nekompatibilní změna configu) a přidá sekci do `CHANGELOG.md`
   (nejdřív anglicky, `---`, pak česky).
4. **Commity:** zpráva ASCII bez diakritiky, `X.Y.Z: co se změnilo` (do ~72 znaků). Agent necommituje ani
   nepushuje sám, pokud ho o to správce výslovně nepožádá.
5. **UI texty nikdy natvrdo** v šablonách ani v kódu: klíč do obou slovníků `app/i18n/*.json`
   (u počtů plurálové pole). Chybějící klíč je chyba.
6. **Demo režim (`demo: true`) ukazuje každou novou funkci** s vymyšlenými daty (`app/demo.py`). Slouží
   k vývoji bez sítě, k testům i ke screenshotům do dokumentace a na blog.
7. **Nic citlivého do repa ani do výstupu:** soukromý SSH klíč, SNMP community, hesla Wi-Fi, skutečné
   IP adresy, MAC a jména zařízení domácnosti nepatří do kódu, ukázek, demo dat, testů ani commitů.
   Ukázky používají `192.168.1.x` a smyšlená jména.

## Bezpečnost (závazné)

- **Bez výslovného souhlasu uživatele nic neměň na skutečných zařízeních** (restart, `uci set`, `wifi`,
  `apk upgrade`, `sysupgrade`, blokace klientů). Čtecí příkazy (`ubus call … status`, `uci show`,
  `swconfig … get`, `snmpwalk`) jsou v pořádku. Pozor: zařízení může být zrovna vypnuté nebo v provozu.
- **Neskenuj síť** (žádné sweepy ani port scany). Ping z routeru jen pro známé IP z ARP (`wake_cmd`).
- **Vstupy do příkazů na zařízení** vždy validuj (regex pro MAC, ifname, IP) a escapuj (`shlex.quote`).
  Hodnoty od uživatele ani z HA nikdy neskládej do shellu bez kontroly.
- **Akce jen pro plný přístup** – kontrola na serveru (`require_full`, role v `request.state.user`),
  ne jen skrytím tlačítka. Zálohy obsahují hesla Wi-Fi a klíče → stažení jen plný přístup.
- **Switche jen čtení** (SNMP v2c GET/GETBULK). Žádné SET.
- Změny SSID vždy s náhledem a potvrzením; blokace klienta jen v paměti `hostapd` (zruší ji restart Wi-Fi).

## Konvence kódu

- Python 3.12, typové anotace, `from __future__ import annotations`. Komentáře v kódu česky **bez
  diakritiky**, identifikátory anglicky. Markdown (`*.md`) a JSON slovníky UTF-8 s diakritikou.
- **Nic blokujícího v event loopu:** síť přes `asyncssh` / asyncio UDP, HTTP přes `asyncio.to_thread`.
- **Parsery výstupů OpenWrt a SNMP jsou čisté funkce** (`parse_*` v `collector.py`, `maint.py`,
  `switch.py`) – vstup text/slovník, výstup data, žádné I/O. Tak se dají testovat na skutečných výstupech.
- Sběr dat nesmí spadnout kvůli jednomu zařízení: chyba se zapíše do `APState.error`, zbytek běží dál.
- Stav, který má přežít restart, patří do `/data` (`wired_seen.json`, `known_devices.json`,
  `updates.json`, `backups/`) a zapisuje se atomicky (`.tmp` + `replace`).
- Šablony: HTMX, akce vrací `_result.html`; výsledky akcí s `hx-preserve`, aby je nesmazal autorefresh.

## Architektura a nezřejmá rozhodnutí

Proč je co tak, jak je – čti, než něco „zjednodušíš“:

- **ARP je klíčované podle IP, ne podle MAC** – router má stejnou MAC v LAN i ve VLAN.
- **Kabelová zařízení na portech AP** = tabulka MAC switche (`swconfig … get arl_table`). Uplink = port
  s MAC routeru, CPU port = tagovaný ve všech `switch_vlan`. MAC vidí víc zařízení → patří **nejhlubšímu**
  (`wired.locate`), z toho plyne i řetězení AP / switch.
- **Switch zapomíná MAC po ~5 min.** Proto paměť „naposledy viděno“ (`wired_seen.json`) a ping tichých
  zařízení z routeru jednou za 4 min (`wake_ips`). Paměť se nemaže, když AP chvíli chybí v configu.
- **Porty jen ze `switch_vlan`** – čip má víc portů než krabice (UniFi AP LR, druhý CPU port AR8327).
  Bez `switch_vlan` porty zařízení z `network.device status`, jen používané sítí.
- **ARP routeru jen z L2 rozhraní** (ne WAN, ne `wg*`, PPPoE, VPN) – brána poskytovatele není klient.
- **Síť zařízení** = rozhraní routeru, do jehož podsítě patří IP (`topology.net_resolver`).
- **Switch přes SNMP = `APState(kind="switch")`** se stejnou tabulkou `arl` jako swconfig → umístění,
  mapa, porty i diagnostika fungují beze změn. Uplink podle MAC routeru (`attach_router_to_switches`).
- **Jména zařízení:** ruční `names` > registr zařízení HA (podle MAC) > DHCP lease > rezervace na routeru.
- **Přítomnost:** Wi-Fi klient, MAC na portu AP (ne stará), nebo ARP. Lease ani rezervace samy nestačí.
- **Upozornění na nová zařízení:** první spuštění = výchozí stav bez upozornění (`known_devices.json`).

## Struktura

```
repository.yaml              # Home Assistant repozitar add-onu
README.md                    # predstaveni repozitare (EN + CZ)
CLAUDE.md, CONTRIBUTING.md   # pravidla a navod pro vyvojare
docs/audit-prompt.md         # zadani technickeho auditu (vystupy do audit/)
openwrt_admin/
  config.yaml                # verze, volby a schema add-onu
  translations/{en,cs}.yaml  # popisy voleb v HA
  DOCS.md, CHANGELOG.md      # dokumentace a zmeny (EN + CZ)
  Dockerfile, requirements.txt
  app/
    main.py                  # FastAPI, Store (stav, poll, udrzba), routy, role
    collector.py             # SSH sber z AP a routeru + parsery
    actions.py               # akce na zarizenich (restart, SSID, apk, kick)
    wired.py                 # umisteni kabelovych zarizeni, uplink, porty
    topology.py              # mapa site (server-side SVG), VLAN, site
    switch.py, snmp.py       # switche pres SNMP, vlastni SNMP v2c klient
    maint.py                 # aktualizace, zalohy, nova zarizeni
    ha.py                    # websocket API Home Assistantu (jmena, notifikace)
    demo.py                  # demo data
    i18n.py, i18n/{cs,en}.json
    templates/, static/
```
