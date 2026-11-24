# Jak na projektu pracovat (pro vývojáře)

Tenhle dokument je pro toho, kdo si repozitář stáhne a chce aplikaci upravovat nebo rozšiřovat.
Uživatelská dokumentace je v [`openwrt_admin/DOCS.md`](openwrt_admin/DOCS.md), článek o projektu na
[tadyk.info](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).

| Dokument | K čemu |
|---|---|
| **tento soubor** | jak začít, spustit demo, testovat, vydat verzi, na co si dát pozor |
| [`CLAUDE.md`](CLAUDE.md) | **závazná pravidla** a nezřejmá architektonická rozhodnutí (pro lidi i AI agenty) |
| [`openwrt_admin/CHANGELOG.md`](openwrt_admin/CHANGELOG.md) | co se v které verzi změnilo (EN + CZ) |
| [`docs/audit-prompt.md`](docs/audit-prompt.md) | zadání pro komplexní technický audit |

## 1. Co to je a jak je to poskládané

Aplikace pro Home Assistant (FastAPI + Jinja2 + HTMX), která přes SSH čte a spravuje OpenWrt AP a router
a přes SNMP čte spravované switche. Data sbírá `Store.poll()` v `app/main.py` každých `poll_interval`
sekund, údržba (aktualizace, zálohy) běží zvlášť jednou za 10 min. Podrobná struktura je v `CLAUDE.md`.

## 2. Prostředí

- Python 3.12 (stejně jako `python:3.12-alpine` v Dockerfile), Linux nebo macOS.
- Virtuální prostředí:
  ```sh
  python3 -m venv .venv
  .venv/bin/pip install -r openwrt_admin/requirements.txt httpx pyyaml playwright
  ```
  (`httpx` pro `TestClient`, `pyyaml` pro kontrolu `config.yaml`, `playwright` jen na screenshoty.)
- Pro simulaci switche: `snmpd` a `snmpwalk` (balíky `snmpd`, `snmp`).

## 3. Spuštění v demo režimu

Demo nepotřebuje síť ani Home Assistant – všechna data jsou vymyšlená (`app/demo.py`).

```sh
mkdir -p /tmp/owdemo
echo '{"demo": true, "language": "cs", "aps": [
  {"name":"Router","host":"192.168.1.1","type":"router"},
  {"name":"AP-Living","host":"192.168.1.2"},
  {"name":"AP-Office","host":"192.168.1.3"},
  {"name":"AP-Garage","host":"192.168.1.4"}]}' > /tmp/owdemo/options.json
cd openwrt_admin
OWADMIN_DATA=/tmp/owdemo ../.venv/bin/uvicorn app.main:app --port 8089
```

Mimo Ingress aplikace pouští jen požadavky z lokální adresy; uživatele nasimuluj hlavičkou
`X-Remote-User-Name: admin` (např. rozšířením prohlížeče nebo v testech).

## 4. Ověření změny

Automatická testovací sada v repozitáři **zatím není** (viz otevřené body níže). Před předáním změny:

1. **Syntaxe:** `python -m py_compile app/*.py`, `config.yaml` a `translations/*.yaml` se načtou přes `yaml.safe_load`.
2. **Demo, všechny stránky v cs i en:** `/`, `/clients`, `/ssids`, `/map`, `/radios`, `/stats`, `/setup`, `/ap/<každé zařízení>`, `/api/stats`
   vrací 200 a v HTML nezůstal žádný nepřeložený klíč (`upd.…`, `map.…` apod.).
3. **Role:** uživatel jen pro čtení nevidí akční tlačítka a akce mu server odmítne (`msg.no_permission`).
4. **Parsery na skutečných výstupech:** nový parser vyzkoušej na výstupu ze skutečného zařízení
   (`arl_table`, `uci show network`, `apk list --upgradable`, `snmpwalk` …). Výstupy anonymizuj.
5. **SSH cesta bez zařízení:** fake SSH server (`asyncssh.create_server` s `process_factory`, který vrací
   připravené výstupy) ověří `collect_ap` / `collect_router` / akce, aniž by se sáhlo na síť.
6. **Switch bez switche:** `snmpd` s `pass` skriptem, který servíruje OID z JSON (data ze skutečného
   `snmpwalk`), na `127.0.0.1:1161`; proti němu `switch.collect_switch()` i celý poll.
7. **Vzhled:** screenshot přes Playwright (světlý i tmavý režim) u změn v UI a v mapě.

**Na skutečné síti** se ověřuje až po nasazení v HA – a jen čtením, akce jen se souhlasem majitele sítě.
V CHANGELOGu poctivě uveď, co běželo naostro a co jen v demu nebo simulaci.

## 5. Pasti, na které jsme narazili

| Past | Co se stane | Jak správně |
|---|---|---|
| ARP klíčované podle MAC | router má stejnou MAC v LAN i VLAN → záznamy se přepíšou, uplink se nenajde | klíč = IP (`parse_arp`) |
| Tichá zařízení na portu AP | switch MAC po ~5 min zapomene, zařízení „uteče“ k routeru | paměť `wired_seen.json` + `wake_ips` |
| Všechny porty ze `swconfig show` | falešné porty (UniFi AP LR, port 6 AR8327 „10M HD“) | jen porty ze `switch_vlan` |
| ARP routeru celé | brána poskytovatele z WAN jako klient | filtr `is_l2_iface` |
| `hx-select` zděděný z autorefreshe | výsledek akce zmizí nebo se vloží celá stránka | `hx-disinherit="*"`, `hx-preserve` |
| Chybí `python-multipart` | aplikace nenaběhne (`Form data requires python-multipart`) | testovat v čistém venv |
| `: ` v popisu v `translations/*.yaml` | neplatný YAML, HA volby nezobrazí | popisy v uvozovkách |
| `from __future__` až za jiným importem | `SyntaxError` | vždy první řádek |
| Výstup pingů na pozadí v jednom příkazu | míchá se do jiných sekcí výstupu routeru | pingy do souborů v `/tmp`, vypsat po `wait` |
| Nové SSH přihlášení pro každé čtení statistik | log dropbearu zaplavený každých 5 s | jedno trvalé spojení (`stats_conn`) |
| Netgear GS724T: SNMP community přes Apply | „Failed to Set …“ u všech polí | nový záznam tlačítkem **Add** |
| Blokace klienta jen na jednom AP | klient se připojí k jinému AP se stejným SSID | blokovat na všech AP s tím SSID |

## 6. Vydání verze

1. `version` v `openwrt_admin/config.yaml` (SemVer).
2. `CHANGELOG.md`: nová sekce `## X.Y.Z` nahoře – anglicky, `---`, česky.
3. Dokumentace: `DOCS.md` (EN + CZ), případně `README.md`, `translations/*.yaml`, slovníky i18n.
4. Commit a push:
   ```sh
   git add -A
   git commit -m "X.Y.Z: co se zmenilo"
   git push
   ```
5. V Home Assistantu `ha store reload` → u aplikace **Aktualizovat**.

## 7. Otevřené body

- Automatické testy (`tests/`, pytest): parsery na anonymizovaných výstupech, `wired.locate` na
  skutečné topologii, smoke test všech stránek v demu (cs + en), kontrola shodných klíčů i18n.
- Kontrola stylu (např. `ruff`) a jednoduché CI na Gitei.
- Nápady a chyby: issues na GitHubu.

## 8. Licence

Projekt je pod licencí [MIT](LICENSE). Kdo pošle změnu (pull request, patch), souhlasí, že ji dává pod stejnou licencí. Nové soubory třetích stran přidávej jen s kompatibilní licencí (MIT, BSD, 0BSD, Apache-2.0) a zapiš je na konec `LICENSE`.
