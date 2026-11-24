# Changelog

Each version in English, then Czech. / Každá verze anglicky, pak česky.

## 0.19.1
- DHCP tab: only `dhcp.apk-new` is shown as a warning; other unmerged `*.apk-new` files (luci, uhttpd, adblock…) are in one collapsed line with an explanation (harmless – apk keeps your configuration) and the commands to compare and delete them.
---
- Záložka DHCP: jako upozornění se ukazuje jen `dhcp.apk-new`; ostatní nesloučené `*.apk-new` (luci, uhttpd, adblock…) jsou v jednom sbaleném řádku s vysvětlením (neškodné – apk tvoji konfiguraci nepřepíše) a příkazy na porovnání a smazání.

## 0.19.0
- New **DHCP** tab on the router page (Overview | Services | DHCP), dnsmasq:
  - **Networks and ranges**: subnet, router address, dynamic range, lease time and usage (static leases inside the range, dynamic leases, free addresses); edit start / count / lease time, turn DHCP on/off, enable DHCP in a network without it. Warning when 5 or fewer addresses are free.
  - **Static leases** (`dhcp.@host`): add, edit, delete, search; online state; tag when the IP is inside the dynamic range. A new IP is suggested as the first free address below the range (not reserved, leased, in ARP or used by a local name). Checks: name, MAC, IP in a router network, not the network/broadcast/router address, IP and MAC not used by another reservation; warnings for an IP leased to another device, inside the range, used by a local name or in a network without DHCP.
  - **Current leases** with **Make static** (fills in name, MAC and IP).
  - Every change: preview of the `uci` commands → Apply → `uci commit dhcp` + `dnsmasq reload`, `uci revert` on error; a configuration backup of the router before the first change of the day; written to the router syslog with the HA user.
  - Warning about unmerged `/etc/config/*.apk-new` (`*-opkg`) files from package upgrades.
- Anonymous sections are read with `uci -X` (real names like `cfg0a1b2c`), so an edit always hits the right lease. IPv6 (odhcpd) is not changed.
- Verified: parser on a real router configuration (5 networks, 39 static leases, 14 local names, `.apk-new`), shell syntax of all commands, demo cs + en (preview, apply, edit, delete, range change, read-only user). Not yet on the real network.
---
- Nová záložka **DHCP** na stránce routeru (Přehled | Služby | DHCP), dnsmasq:
  - **Sítě a rozsahy**: podsíť, adresa routeru, dynamický rozsah, doba zápůjčky a obsazenost (statické zápůjčky v rozsahu, dynamické, volné); úprava začátku / počtu / doby, zapnutí a vypnutí DHCP, zapnutí DHCP v síti bez něj. Upozornění, když zbývá 5 a méně volných adres.
  - **Statické zápůjčky** (`dhcp.@host`): přidat, upravit, smazat, hledat; stav online; štítek, když je IP uvnitř dynamického rozsahu. Nová IP se navrhne jako první volná pod rozsahem (mimo rezervace, zápůjčky, ARP a místní jména). Kontroly: jméno, MAC, IP v síti routeru, ne adresa sítě/broadcast/routeru, IP a MAC nemá jiná rezervace; varování, když IP má zapůjčené jiné zařízení, je v rozsahu, u místního jména nebo v síti bez DHCP.
  - **Aktuální zápůjčky** s tlačítkem **Udělat statickou** (doplní jméno, MAC a IP).
  - Každá změna: náhled příkazů `uci` → Použít → `uci commit dhcp` + `dnsmasq reload`, při chybě `uci revert`; před první změnou dne záloha konfigurace routeru; zápis do syslogu routeru se jménem uživatele HA.
  - Upozornění na nesloučené soubory `/etc/config/*.apk-new` (`*-opkg`) po aktualizaci balíčků.
- Anonymní sekce se čtou přes `uci -X` (skutečná jména jako `cfg0a1b2c`), takže úprava vždy trefí správnou zápůjčku. IPv6 (odhcpd) se nemění.
- Ověřeno: parser na skutečné konfiguraci routeru (5 sítí, 39 statických zápůjček, 14 místních jmen, `.apk-new`), syntaxe všech příkazů v shellu, demo cs + en (náhled, použití, úprava, smazání, změna rozsahu, uživatel jen pro čtení). Na skutečné síti zatím ne.

## 0.18.2
- Faster **Services** tab: "starts on boot" is read from `/etc/rc.d` instead of running `/etc/init.d/<x> enabled` for each of ~40 scripts (took seconds on the router); the adblock search command is detected from the init script without running it.
- The tab opens immediately and the data is loaded into it ("Loading…"); within 60 s of the last read (and after every action) it is shown straight from memory.
---
- Rychlejší záložka **Služby**: „spouští se po startu“ se čte z `/etc/rc.d` místo spouštění `/etc/init.d/<x> enabled` u každého z ~40 skriptů (na routeru to trvalo sekundy); příkaz hledání v adblocku se pozná z init skriptu bez jeho spuštění.
- Záložka se otevře hned a data se do ní dotáhnou („Načítám…“); do 60 s od posledního načtení (a po každé akci) se ukáže rovnou z paměti.

## 0.18.1
- Services moved from a separate page to a **Services tab on each device** (router and AP page: Overview | Services) – every action applies only to that device. The old `/services` address redirects to the router.
---
- Služby se přesunuly ze samostatné stránky na **záložku Služby u každého zařízení** (stránka routeru a AP: Přehled | Služby) – každá akce platí jen pro to zařízení. Stará adresa `/services` přesměruje na router.

## 0.18.0
- New **Services** page (since 0.18.1 a tab on each device): services on the router and every AP (`ubus call service list` + `/etc/init.d/* enabled`) – running / finished / stopped / failed, start on boot; full access: start, stop, restart, enable, disable with a command preview. `dropbear`, `network`, `rpcd`, `log`… can only be restarted (in the background) so the app never locks itself out. Status is read only when the page opens (nothing in the device log).
- **adblock** (when installed): state (blocking / not blocking / paused / disabled), blocked domains, lists, last run. Detects "enabled but the blocklist is empty" (paused elsewhere or lists not downloaded after boot). **Pause** for 15 min / 1 h / 4 h (resumed by a timer on the router itself, survives a Home Assistant restart) or until resumed, **Resume**, **Reload lists**, **Why is it blocked?** (`adblock search`, older `query`), **Unblock / Block domain** (`adblock.allowlist` / `adblock.blocklist`, then reload), lists with removal.
- **banIP** (when installed): state, **Why is it blocked?** (`banip search`), **Unblock / Block IP or network** (`banip.allowlist` / `banip.blocklist`, reload only when banIP is enabled), lists with removal; a disabled or failed banIP is shown as such.
- Every action goes through preview → Apply and is written to the device syslog with the HA user (`logread -e openwrt-admin`). Search is read-only and allowed for read-only users too.
- Verified: parsers on real adblock 4.5.9 / banIP 1.9.2 output (empty list, disabled banIP), shell syntax of all commands, list add/remove on files, demo cs + en (preview, apply, search). Not yet on the real network.
---
- Nová stránka **Služby** (od 0.18.1 záložka u každého zařízení): služby na routeru a každém AP (`ubus call service list` + `/etc/init.d/* enabled`) – běží / dokončeno / zastaveno / selhalo, spouštění po startu; plný přístup: spustit, zastavit, restart, zapnout/vypnout po startu s náhledem příkazu. `dropbear`, `network`, `rpcd`, `log`… jdou jen restartovat (na pozadí), aby se aplikace sama neodřízla. Stav se čte jen při otevření stránky (nic do logu zařízení).
- **adblock** (když je nainstalovaný): stav (blokuje / neblokuje / pozastaveno / vypnuto), počet blokovaných domén, seznamy, poslední běh. Pozná „zapnutý, ale blokovací seznam je prázdný“ (pozastaveno odjinud nebo se po startu nestáhly seznamy). **Pozastavit** na 15 min / 1 h / 4 h (obnoví časovač přímo na routeru, přežije restart Home Assistantu) nebo do obnovení, **Obnovit blokování**, **Znovu načíst seznamy**, **Proč je blokovaná?** (`adblock search`, starší `query`), **Odblokovat / Zablokovat doménu** (`adblock.allowlist` / `adblock.blocklist`, pak reload), seznamy s odebráním.
- **banIP** (když je nainstalovaný): stav, **Proč je blokovaná?** (`banip search`), **Odblokovat / Zablokovat IP nebo síť** (`banip.allowlist` / `banip.blocklist`, reload jen když je banIP zapnutý), seznamy s odebráním; vypnutý nebo neběžící banIP je tak i označený.
- Každá akce jde přes náhled → Použít a zapíše se do syslogu zařízení se jménem uživatele HA (`logread -e openwrt-admin`). Hledání je jen čtení, smí i uživatel s přístupem jen pro čtení.
- Ověřeno: parsery na skutečném výstupu adblock 4.5.9 / banIP 1.9.2 (prázdný seznam, vypnutý banIP), syntaxe všech příkazů v shellu, přidání/odebrání v seznamech na souborech, demo cs + en (náhled, použití, hledání). Na skutečné síti zatím ne.

## 0.17.10
- **Dedicated SSH user:** documentation and Settings page show how to create a user (e.g. `owadmin`) with root rights (UID 0, no password, nothing installed) instead of logging in as `root`; new per-device option `user` in `aps` (overrides `ssh_user`).
- **Who did what:** every action started from the app (reboot, SSID, new SSID, radios, `apk`, backup, scan, client disconnect/block) is written to the device syslog with the tag `openwrt-admin` and the Home Assistant user name (`logread -e openwrt-admin`); passwords are masked.
- Wording: Home Assistant renamed add-ons to **apps** (2026.2) – documentation, UI and option descriptions now say "app" (technical names like `/addons`, slug and `ha store` stay).
---
- **Vlastní SSH uživatel:** dokumentace a stránka Nastavení ukazují, jak vytvořit uživatele (např. `owadmin`) s právy root (UID 0, bez hesla, nic se neinstaluje) místo přihlašování jako `root`; nová volba `user` u každého zařízení v `aps` (přebije `ssh_user`).
- **Kdo co dělal:** každá akce spuštěná z aplikace (restart, SSID, nové SSID, rádia, `apk`, záloha, sken, odpojení/blokace klienta) se zapíše do syslogu zařízení s tagem `openwrt-admin` a jménem uživatele Home Assistantu (`logread -e openwrt-admin`); hesla jsou skrytá.
- Názvosloví: Home Assistant přejmenoval add-ony na **aplikace** (2026.2) – dokumentace, UI a popisy voleb teď říkají „aplikace“ (technické názvy jako `/addons`, slug a `ha store` zůstávají).

## 0.17.9
- **Edit radios** on the Radios page (full access): channel, width, transmit power for each radio and the country for all, with options from `iwinfo freqlist`/`txpowerlist`/`htmodelist` (DFS marked; old iwinfo without htmodelist: widths by the radio family). Preview of the `uci` commands per AP → **Apply** (`uci commit wireless && wifi reload`, `uci revert` on error).
- **Propose settings**: pre-fills the form with a channel plan for all APs (own APs on different channels, away from neighbours from the last scan, optionally DFS), the widest sensible width and power balance (5 GHz max, 2.4 GHz 3 dB lower) – each change with a reason.
- **Add a new SSID** on the SSID page: name, password, WPA2 / WPA2+WPA3 / WPA3, network, hidden, on selected radios of any APs (new `wifi-iface` sections `owadmin_<ssid>_<radio>`), with a preview; the password is never shown or logged.
- The APs now also report allowed channels, transmit powers and widths (`iwinfo freqlist/txpowerlist/htmodelist`, once per radio).
- Verified: parsers on real `iwinfo` output (AR9580: freqlist with flags, txpowerlist, empty htmodelist), the shell command with simulated sysfs/iwinfo/iw, preview/apply and validation in the demo (cs + en). Not yet on the real network.
---
- **Upravit rádia** na stránce Rádia (plný přístup): kanál, šířka, vysílací výkon každého rádia a země pro všechna, s možnostmi z `iwinfo freqlist`/`txpowerlist`/`htmodelist` (DFS označené; starý iwinfo bez htmodelist: šířky podle rodiny rádia). Náhled příkazů `uci` pro každé AP → **Použít** (`uci commit wireless && wifi reload`, při chybě `uci revert`).
- **Navrhnout nastavení**: předvyplní formulář návrhem kanálů pro všechna AP (vlastní AP na různých kanálech, mimo sousedy z posledního skenu, volitelně DFS), nejširší rozumnou šířkou a vyvážením výkonu (5 GHz max., 2,4 GHz o 3 dB méně) – u každé změny důvod.
- **Přidat nové SSID** na stránce SSID: název, heslo, WPA2 / WPA2+WPA3 / WPA3, síť, skrytí, na vybraných rádiích libovolných AP (nové sekce `wifi-iface` `owadmin_<ssid>_<rádio>`), s náhledem; heslo se nikde nezobrazuje ani neloguje.
- AP nově hlásí i povolené kanály, výkony a šířky (`iwinfo freqlist/txpowerlist/htmodelist`, jednou za rádio).
- Ověřeno: parsery na skutečném výstupu `iwinfo` (AR9580: freqlist s příznaky, txpowerlist, prázdný htmodelist), příkaz pro shell se simulovaným sysfs/iwinfo/iw, náhled/použití a validace v demu (cs + en). Na skutečné síti zatím ne.

## 0.17.1
- Optional **neighbour scan** on the Radios page (option `radio_scan`, off by default, full access): **Scan** per radio or for all radios (`iwinfo <if> scan`). Shows neighbouring networks (SSID, BSSID, channel, width, signal, encryption; own APs marked by BSSID/SSID), channel occupancy bars in the channel map, warnings about strong foreign networks on your channel and a less busy channel suggestion (2.4 GHz 1/6/11, 5 GHz 36–48 without DFS).
- Radios read also their BSSIDs from `iwinfo` (recognising own APs in scans).
- Verified: parser on a real scan from an AP (2.4 + 5 GHz, HT40 above/below, VHT80), demo cs + en. Not yet on the real network.
---
- Volitelný **sken okolí** na stránce Rádia (volba `radio_scan`, výchozí vypnuto, plný přístup): **Skenovat** u každého rádia nebo pro všechna (`iwinfo <if> scan`). Ukáže okolní sítě (SSID, BSSID, kanál, šířka, signál, šifrování; vlastní AP označené podle BSSID/SSID), sloupce obsazenosti kanálů v mapě kanálů, upozornění na silné cizí sítě na tvém kanálu a návrh méně obsazeného kanálu (2,4 GHz 1/6/11, 5 GHz 36–48 bez DFS).
- Rádia čtou z `iwinfo` i svoje BSSID (poznání vlastních AP ve skenu).
- Ověřeno: parser na skutečném skenu z AP (2,4 + 5 GHz, HT40 above/below, VHT80), demo cs + en. Na skutečné síti zatím ne.

## 0.17.0
- New **Radios** page: all radios of all APs in one table with the real channel (also with `auto`), width, transmit power, noise and chip from `iwinfo`, and **channel utilisation** (now, average, chart of the last hour) from `iw survey`.
- Warnings and tips: own APs on the same/overlapping channel, 2.4 GHz outside 1/6/11 or 40 MHz wide, 5 GHz only 20 MHz, channel `auto`, DFS, busy channel, high noise, 2.4 GHz stronger than 5 GHz, different/missing country.
- Channel map per band (occupied channels with width, 1/6/11 and DFS highlighted).
- AP page: real channel, width, power and channel utilisation in the radio table. HA sensors: channel utilisation per radio.
- Verified: parsers on real `iwinfo` and `iw survey` output (AR9340/AR9580), the shell command against a simulated sysfs, demo cs + en. Not yet on the real network.
---
- Nová stránka **Rádia**: všechna rádia všech AP v jedné tabulce se skutečným kanálem (i u `auto`), šířkou, vysílacím výkonem, šumem a čipem z `iwinfo` a **vytížením kanálu** (teď, průměr, graf za hodinu) z `iw survey`.
- Upozornění a tipy: vlastní AP na stejném/překrývajícím se kanálu, 2,4 GHz mimo 1/6/11 nebo 40 MHz, 5 GHz jen 20 MHz, kanál `auto`, DFS, vytížený kanál, vysoký šum, 2,4 GHz silnější než 5 GHz, různá/chybějící země.
- Mapa kanálů pro každé pásmo (zabrané kanály i se šířkou, zvýrazněné 1/6/11 a DFS).
- Stránka AP: skutečný kanál, šířka, výkon a vytížení kanálu v tabulce rádií. Senzory HA: vytížení kanálu každého rádia.
- Ověřeno: parsery na skutečném výstupu `iwinfo` a `iw survey` (AR9340/AR9580), příkaz pro shell proti simulovanému sysfs, demo cs + en. Na skutečné síti zatím ne.

## 0.16.0
- New option `new_device_hours` (default 24, 0 = off): how long a device has the "new" tag; shown on the Settings page.
- Fix: charts and the ping history were black after updating – the browser (Ingress) kept the old `style.css` in its cache. Static files now have a version in their URL, and the charts carry their colours also directly in the SVG.
- Statistics: several interfaces on one device (e.g. `wan` + `wan6` on `eth0`) are shown as one row (the active one).
---
- Nová volba `new_device_hours` (výchozí 24, 0 = vypnuto): jak dlouho má zařízení štítek „nové“; vidět i na stránce Nastavení.
- Oprava: grafy a historie pingu byly po aktualizaci černé – prohlížeč (Ingress) držel v cache staré `style.css`. Statické soubory mají v adrese verzi a grafy mají barvy i přímo v SVG.
- Statistiky: víc rozhraní na jednom zařízení (např. `wan` + `wan6` na `eth0`) je jeden řádek (to zapnuté).

## 0.15.0
- New **Statistics** page: router interface speeds (WAN, LAN, every VLAN) with packets/s, peak and a chart of the last hour, router CPU temperature and load, ping to internet targets (`ping_targets`), the ISP gateway, APs and switches with loss and history, top internet users (`top_clients`) and the busiest switch ports. History of the last hour in memory.
- Top internet users from **nlbwmon** when it runs on the router (nothing is installed), otherwise from **conntrack** (`traffic_source`: auto / nlbwmon / conntrack / off).
- Router and switch counters are read every `stats_interval` seconds (default 5) over one open SSH connection; switch port traffic over SNMP (`ifHCInOctets`/`ifHCOutOctets`) – also shows traffic inside the network, e.g. laptop ↔ server.
- Map: current traffic (`↓`/`↑`) on the lines to APs, switches and the internet and on wired devices on switch ports; over half of the port speed red and thick. Switch page: *Traffic* column; router page: RX/TX now; client tables: *Now ↓/↑*; overview: *Internet* and *WAN* tiles.
- Optional sensors in Home Assistant (`ha_sensors`, `ha_sensors_interval`): speeds, packets, ping, internet connectivity, CPU temperature, top clients and switch ports as `sensor.openwrt_admin_*` / `binary_sensor.openwrt_admin_*` (REST API, for history and InfluxDB/Grafana).
- New `/api/stats` (JSON). Demo mode shows all of it with an hour of made-up history; demo Wi-Fi clients have stable MACs.
- Map legend: the port speed sample (`1G 100M`) was stretched over the whole width.
- Verified: unit tests of the parsers, the whole router command in a real shell over a fake SSH server, demo in cs + en. Not yet on the real network.
---
- Nová stránka **Statistiky**: rychlosti rozhraní routeru (WAN, LAN, každá VLAN) s pakety/s, špičkou a grafem za poslední hodinu, teplota CPU a zátěž routeru, ping na cíle v internetu (`ping_targets`), bránu poskytovatele, AP a switche se ztrátou a historií, kdo nejvíc vytěžuje internet (`top_clients`) a nejvytíženější porty switche. Historie poslední hodiny v paměti.
- Kdo vytěžuje internet: z **nlbwmon**, když na routeru běží (nic se neinstaluje), jinak z **conntracku** (`traffic_source`: auto / nlbwmon / conntrack / off).
- Router a čítače switchů se čtou každých `stats_interval` sekund (výchozí 5) přes jedno trvalé SSH spojení; provoz na portech switche přes SNMP (`ifHCInOctets`/`ifHCOutOctets`) – je vidět i provoz uvnitř sítě, např. notebook ↔ server.
- Mapa: aktuální provoz (`↓`/`↑`) na čarách k AP, switchům a do internetu a u kabelových zařízení na portech switche; nad polovinou rychlosti portu červeně a silně. Stránka switche: sloupec *Provoz*; stránka routeru: RX/TX teď; tabulky klientů: *Teď ↓/↑*; přehled: dlaždice *Internet* a *WAN*.
- Volitelné senzory v Home Assistantu (`ha_sensors`, `ha_sensors_interval`): rychlosti, pakety, ping, dostupnost internetu, teplota CPU, top klienti a porty switche jako `sensor.openwrt_admin_*` / `binary_sensor.openwrt_admin_*` (REST API, pro historii a InfluxDB/Grafanu).
- Nové `/api/stats` (JSON). Demo ukazuje všechno s hodinou vymyšlené historie; Wi-Fi klienti v demu mají stálé MAC.
- Legenda mapy: ukázka rychlosti portu (`1G 100M`) byla roztažená přes celou šířku.
- Ověřeno: testy parserů, celý příkaz pro router ve skutečném shellu přes simulovaný SSH server, demo cs + en. Na skutečné síti zatím ne.

## 0.14.2
- Map: every AP or switch plugged into another device (e.g. into the switch) has its own line from it instead of one shared vertical line; lower devices are further left so the lines do not cross.
- Demo: both APs are plugged directly into the switch.
---
- Mapa: každé AP nebo switch zapojené do jiného zařízení (např. do switche) má z něj vlastní čáru místo jedné společné svislé; níže položená zařízení jsou víc vlevo, takže se čáry nekříží.
- Demo: obě AP jsou zapojená přímo do switche.

## 0.14.1
- Map: the group of wired devices under a switch is called "LAN" like under APs (was "Ports").
---
- Mapa: skupina kabelových zařízení pod switchem se jmenuje „LAN“ jako u AP (místo „Porty“).

## 0.14.0
- Managed switches over SNMP (optional `switches`: name, host, community, port): read-only SNMP v2c with standard MIBs (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB) – works with Netgear, TP-Link, Cisco SG, Zyxel, HP… No extra library, the SNMP client is built in.
- The switch appears on the map between the router and the APs: uplink by the router's MAC, APs and wired devices placed on its ports with port speed, devices coloured by network. Own page with ports (speed, VLANs untagged/tagged, devices) and MAC table diagnostics; card on the overview and a tab.
- Big switches: the port table lists only connected ports (all ports stay as boxes).
---
- Spravované switche přes SNMP (volitelné `switches`: name, host, community, port): jen čtení přes SNMP v2c se standardními MIB (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB) – funguje s Netgear, TP-Link, Cisco SG, Zyxel, HP… Bez další knihovny, SNMP klient je vestavěný.
- Switch je na mapě mezi routerem a AP: uplink podle MAC routeru, AP a kabelová zařízení na jeho portech s rychlostí portu, zařízení obarvená podle sítě. Vlastní stránka s porty (rychlost, VLANy netagované/tagované, zařízení) a diagnostikou tabulky MAC; karta na přehledu a záložka.
- Velké switche: tabulka portů ukazuje jen připojené porty (všechny zůstávají jako políčka).

## 0.13.1
- Ports: only switch ports used in `switch_vlan` are shown – the switch chip often has more ports than the device (UniFi AP LR: 5 chip ports, 1 jack; AR8327: port 6 is a second CPU port). Without `switch_vlan` the switch is unused and the device's own ports are shown (e.g. `eth0`), only those used by the network.
- Map: 1 Gb/s speeds are green.
---
- Porty: zobrazují se jen porty switche použité ve `switch_vlan` – čip switche má často víc portů, než je na krabici (UniFi AP LR: 5 portů čipu, 1 zásuvka; AR8327: port 6 je druhý CPU port). Bez `switch_vlan` se switch nepoužívá a ukážou se porty zařízení (např. `eth0`), jen ty, které síť používá.
- Mapa: rychlost 1 Gb/s je zeleně.

## 0.13.0
- Port speeds: AP and router pages have a new **Ports** panel – every physical port with its state, negotiated speed (1 Gb/s, 100 Mb/s…) and duplex, role (uplink, CPU, LAN, WAN) and the devices on it. Source: `swconfig dev <switch> show` (switch ports) or `ubus call network.device status` (DSA ports, separate network cards).
- Map: the line to each AP shows the speed of its uplink port (e.g. "1G", "100M"), wired devices on AP ports show their port speed. Below 1 Gb/s is orange (often a bad or 4-wire cable).
---
- Rychlost portů: stránka AP i routeru má nový panel **Porty** – každý fyzický port se stavem, vyjednanou rychlostí (1 Gb/s, 100 Mb/s…) a duplexem, rolí (uplink, CPU, LAN, WAN) a zařízeními na něm. Zdroj: `swconfig dev <switch> show` (porty switche) nebo `ubus call network.device status` (porty DSA, samostatné síťové karty).
- Mapa: u čáry ke každému AP je rychlost jeho uplink portu (např. „1G“, „100M“), kabelová zařízení na portech AP mají rychlost svého portu. Pod 1 Gb/s oranžově (často vadný nebo 4žilový kabel).

## 0.12.1
- Blocked Wi-Fi clients are listed (Clients page and AP page, from hostapd `list_bans`) with the remaining time and an **Unblock** button (full access).
- Dark mode: drop-down lists (e.g. Disconnect…) were white.
---
- Zablokovaní Wi-Fi klienti jsou vidět (stránka Klienti a stránka AP, z hostapd `list_bans`) se zbývajícím časem a tlačítkem **Zrušit blokaci** (plný přístup).
- Tmavý režim: rozbalovací seznamy (např. Odpojit…) byly bílé.

## 0.12.0
- New devices: when a device the add-on has never seen joins the network (Wi-Fi, AP port or router ARP), a notification is sent to Home Assistant, optionally also to a `notify.*` service (e.g. the mobile app). The first start takes all present devices as known. New devices have a "new" tag for 24 hours. Options `notify_new_devices`, `notify_service`, `notify_random_mac`.
- Updates overview: every `update_check_hours` (default 12) each device is checked for packages to update; the overview shows the count (tile + cards), the device page lists the packages. A newer OpenWrt release is detected from downloads.openwrt.org.
- Configuration backups: **Back up now** on AP/router pages and an automatic backup every `backup_interval_days` (default 7) via `sysupgrade -b`, stored in `/data/backups` (part of HA backups), the last `backup_keep` (10) kept. Download only with full access (contains Wi-Fi passwords).
- Wi-Fi clients can be disconnected or blocked for 10 min / 1 h / 24 h (on all APs with the same SSID) from the client tables – full access only.
- Settings page: status of notifications, update checks and backups.
---
- Nová zařízení: když se v síti objeví zařízení, které add-on ještě neviděl (Wi-Fi, port AP nebo ARP routeru), pošle upozornění do Home Assistantu, volitelně i do služby `notify.*` (např. mobilní aplikace). První spuštění bere všechna přítomná zařízení jako známá. Nová zařízení mají 24 hodin štítek „nové“. Volby `notify_new_devices`, `notify_service`, `notify_random_mac`.
- Přehled aktualizací: každých `update_check_hours` (výchozí 12) se na každém zařízení zjistí balíčky k aktualizaci; přehled ukazuje počet (dlaždice + karty), stránka zařízení je vypíše. Novější verze OpenWrt se zjišťuje z downloads.openwrt.org.
- Zálohy konfigurace: **Zálohovat teď** na stránce AP/routeru a automatická záloha každých `backup_interval_days` (výchozí 7) přes `sysupgrade -b`, ukládá se do `/data/backups` (součást záloh HA), drží se posledních `backup_keep` (10). Stáhnout jen s plným přístupem (obsahuje hesla Wi-Fi).
- Wi-Fi klienty lze z tabulek klientů odpojit nebo zablokovat na 10 min / 1 h / 24 h (na všech AP se stejným SSID) – jen plný přístup.
- Stránka Nastavení: stav upozornění, kontroly aktualizací a záloh.

## 0.11.0
- Map: wired devices at the router (not on Wi-Fi or AP ports) are split by network – one branch per LAN/VLAN (e.g. "VLAN 100 · dmz") in its color, matched by the router interface whose subnet contains the device IP. Empty networks are hidden.
- Router page: new "Network" column in the device list and the number of active devices per interface.
- The router's ARP entries on WAN and tunnel/VPN interfaces (e.g. the ISP gateway, `wg0`) are no longer treated as clients; WireGuard and other tunnels are left out of the map networks.
- AP page: collapsible "Switch diagnostics" – ports with MAC addresses, which port is the uplink and the CPU, whether the AP sees the router, and the "last seen" memory for this AP.
- Quiet wired devices: the switch forgets a MAC after about 5 minutes without traffic, so a rarely talking device (e.g. a Cisco phone) jumped to the router. The router now pings wired devices with an unknown place once every 4 minutes, so the AP switch learns them again.
- The "last seen" memory is no longer erased when an AP is briefly missing from the configuration (e.g. while being renamed).
---
- Mapa: kabelová zařízení u routeru (mimo Wi-Fi a porty AP) jsou rozdělená podle sítě – každá LAN/VLAN má vlastní větev ve své barvě (např. „VLAN 100 · dmz“), podle rozhraní routeru, do jehož podsítě patří IP zařízení. Prázdné sítě se nezobrazují.
- Stránka routeru: nový sloupec „Síť“ v seznamu zařízení a počet aktivních zařízení u každého rozhraní.
- Záznamy ARP routeru na WAN a tunelových/VPN rozhraních (např. brána poskytovatele, `wg0`) už se neberou jako klienti; WireGuard a jiné tunely nejsou mezi sítěmi na mapě.
- Stránka AP: sbalitelná „Diagnostika switche“ – porty s MAC adresami, který port je uplink a CPU, jestli AP vidí router, a paměť „naposledy viděno“ pro tento AP.
- Tichá kabelová zařízení: switch MAC adresu po zhruba 5 minutách bez provozu zapomene, takže zařízení, které se ozývá jen občas (např. telefon Cisco), přeskakovalo k routeru. Router teď kabelová zařízení s neznámým místem jednou za 4 minuty pingne a switch AP se je znovu naučí.
- Paměť „naposledy viděno“ se už nemaže, když AP na chvíli zmizí z konfigurace (např. při přejmenování).

## 0.10.1
- Fix: since 0.10.0 static DHCP reservations made switched-off devices and Wi-Fi devices that were currently away show up under "LAN / other" on the map. Presence is now decided only by Wi-Fi, switch ports and ARP tables; reservations are used just for names. The router page shows inactive devices in a collapsed "Inactive" section.
---
- Oprava: od 0.10.0 se kvůli statickým rezervacím DHCP ukazovala v „LAN / ostatní“ na mapě i vypnutá zařízení a Wi-Fi zařízení, která zrovna nebyla připojená. O přítomnosti teď rozhoduje jen Wi-Fi, porty switche a ARP tabulky; rezervace slouží jen pro jména. Stránka routeru má neaktivní zařízení ve sbalené sekci „Neaktivní“.

## 0.10.0
- Device names from more sources: `names` in the configuration (manual) → Home Assistant device registry by MAC (ESPHome, Shelly…; `homeassistant_api: true`, option `ha_names`) → DHCP lease → static DHCP reservation on the router. Names show a small tag with their source.
- IPs for devices with a static address from the ARP tables of the router and APs; the router page lists all devices the router knows, marked "static IP".
- Settings page: overview of name sources. New dependency `websockets`.
---
- Jména zařízení z více zdrojů: `names` v konfiguraci (ručně) → registr zařízení Home Assistantu podle MAC (ESPHome, Shelly…; `homeassistant_api: true`, volba `ha_names`) → DHCP → statická rezervace DHCP na routeru. U jména je štítek se zdrojem.
- IP i pro zařízení s pevnou adresou z ARP tabulek routeru a AP; stránka routeru ukazuje všechna zařízení, která router zná, s označením „pevná IP“.
- Stránka Nastavení: přehled zdrojů jmen. Nová závislost `websockets`.

## 0.9.2
- Map: the "Wired" group under APs and the router's "Wired / other" node are now called "LAN" and "LAN / other".
- Last known place of wired devices: when a device disappears from the switch MAC table (e.g. TV switched off), it stays at its AP and port, greyed out with "last seen …", while it has a DHCP lease (otherwise 24 h). Stored in `/data/wired_seen.json`.
---
- Mapa: skupina „Kabel“ pod AP a uzel „Kabel / ostatní“ u routeru se jmenují „LAN“ a „LAN / ostatní“.
- Poslední známé místo kabelových zařízení: když zařízení zmizí z tabulky MAC adres switche (např. vypnutá televize), zůstane u svého AP a portu šedě s „naposledy viděno …“, dokud má DHCP záznam (jinak 24 h). Ukládá se do `/data/wired_seen.json`.

## 0.9.1
- Fix: wired devices on AP ports were not found when the router had the same MAC on several IPs (LAN + VLAN interfaces) – the ARP table is now keyed by IP.
---
- Oprava: kabelová zařízení na portech AP se nenašla, když měl router stejnou MAC na více IP (LAN + VLAN rozhraní) – ARP tabulka se teď ukládá podle IP.

## 0.9.0
- Map: wired devices on AP ports (APs that also work as switches) are shown under that AP in a "Wired" group with the port number; an AP connected through another AP is drawn under it. Source: switch MAC table (`swconfig … get arl_table`), uplink = port with the router's MAC, CPU port = tagged in all `switch_vlan`s.
- Router page: for each DHCP lease shows the AP and port it is wired to.
- Lines to Wi-Fi clients are dotted, wired ones solid.
---
- Mapa: kabelová zařízení na portech AP (AP fungují i jako switch) se ukážou pod tím AP ve skupině „Kabel“ s číslem portu; AP zapojený přes jiný AP se nakreslí pod ním. Zdroj: tabulka MAC adres switche (`swconfig … get arl_table`), uplink = port s MAC routeru, CPU port = tagovaný ve všech `switch_vlan`.
- Stránka routeru: u DHCP klientů je vidět, ke kterému AP a portu jsou připojení kabelem.
- Čáry ke klientům na Wi-Fi jsou tečkované, kabelové plné.

## 0.8.0
- Map: clients as their own nodes (name/MAC + signal, wired devices with IP, tooltip with IP/MAC/band), SSIDs without clients hidden by default (toggle), clients can be switched off.
- Map can be zoomed (mouse wheel around the cursor, + / − / Fit) and panned by dragging; position is kept across automatic refresh.
---
- Mapa: klienti jako samostatné uzly (jméno/MAC + signál, zařízení na kabelu s IP, po najetí IP/MAC/pásmo), SSID bez klientů jsou ve výchozím stavu skrytá (přepínač), klienty jde vypnout.
- Mapu jde přibližovat (kolečko myši kolem kurzoru, + / − / Vše) a posouvat tažením; pozice zůstane i po automatickém obnovení.

## 0.7.2
- Fix: on APs, VLAN interfaces with their own gateway (e.g. `proto dhcp`, or static with `gateway`) were treated as WAN and missing from the map. On APs only interfaces named `wan*` are WAN now.
---
- Oprava: na AP se VLAN rozhraní s vlastní bránou (např. `proto dhcp` nebo statická s `gateway`) brala jako WAN a na mapě chyběla. Na AP je teď WAN jen rozhraní pojmenované `wan*`.

## 0.7.1
- Only defined devices: each device in the list has `type: ap` (default) or `type: router`. `dhcp_router`/`router_name` are deprecated; if `dhcp_router` points to a device from the list, that device becomes the router (no more duplicate router on the map).
- Map: VLAN detection also from `uci show network` (older OpenWrt, `ifname='eth0.30'`), management interface (device IP / `lan`) always untagged LAN, fallback by router interface name. Router shows its VLAN chips; chips no longer overlap the IP.
---
- Jen definovaná zařízení: každé zařízení v seznamu má `type: ap` (výchozí) nebo `type: router`. `dhcp_router`/`router_name` jsou zastaralé; když `dhcp_router` míří na zařízení ze seznamu, stane se routerem to zařízení (na mapě už není router dvakrát).
- Mapa: VLAN se zjišťuje i z `uci show network` (starší OpenWrt, `ifname='eth0.30'`), management rozhraní (IP zařízení / `lan`) je vždy netagovaná LAN, záložně podle názvu rozhraní na routeru. Router má štítky svých VLAN; štítky už nepřekrývají IP.

## 0.7.0
- New **Map** page: network tree Internet → router → APs → SSIDs (+ wired clients). Lines distinguish WAN, untagged LAN, trunk with VLANs (VLAN chips in AP nodes) and Wi-Fi; colours per network (LAN / VLAN ID), legend with interface names. Offline devices shown dashed red. Nodes link to their pages.
- APs now also read `network.interface dump` and `network.device status`; VLAN IDs detected from device names and bridge members (swconfig and DSA).
---
- Nová stránka **Mapa**: strom sítě Internet → router → AP → SSID (+ klienti na kabelu). Spojnice rozlišují WAN, netagovanou LAN, trunk s VLANy (štítky VLAN v uzlu AP) a Wi-Fi; barvy podle sítě (LAN / VLAN ID), legenda s názvy rozhraní. Offline zařízení červeně přerušovaně. Uzly jsou odkazy na své stránky.
- AP nově čtou i `network.interface dump` a `network.device status`; VLAN ID se zjišťuje z názvů zařízení a členů bridge (swconfig i DSA).

## 0.6.0
- The router is a device of its own: tab, overview card and page with system info, interfaces (WAN/LAN/VLANs from `network.interface dump`) and all DHCP leases including wired devices (Wi-Fi clients show their AP/SSID). Reboot and package updates work on the router too. One SSH connection per poll.
- New option `router_name` (default Router).
---
- Router je samostatné zařízení: záložka, karta na přehledu a stránka s informacemi o systému, rozhraními (WAN/LAN/VLANy z `network.interface dump`) a všemi DHCP klienty včetně zařízení na kabelu (u Wi-Fi klientů AP/SSID). Restart a aktualizace balíčků fungují i na routeru. Jedno SSH spojení na jeden sběr dat.
- Nová volba `router_name` (výchozí Router).

## 0.5.0
- Web UI translated: English and Czech, texts in dictionaries `app/i18n/<lang>.json` (a new language = a new file). Language: per-browser choice on the Settings page → `language` option → Home Assistant user's language → browser → English.
- Overview: "AP online" became "devices online" and includes the DHCP router; router status on the Settings page.
- Add-on icon and logo (`icon.png`, `logo.png`).
- Band shown as "2.4 GHz" in English.
---
- Webové rozhraní přeložené: angličtina a čeština, texty ve slovnících `app/i18n/<jazyk>.json` (nový jazyk = nový soubor). Jazyk: volba v prohlížeči na stránce Nastavení → volba `language` → jazyk uživatele Home Assistantu → prohlížeč → angličtina.
- Přehled: z „AP online“ je „zařízení online“ a počítá se i DHCP router; stav routeru na stránce Nastavení.
- Ikona a logo add-onu (`icon.png`, `logo.png`).

## 0.4.0
- Page refresh interval is configurable: `refresh_interval` in the add-on configuration (0 = off) and per browser on the Settings page.
- Default configuration and demo data use example devices (AP-Living, AP-Office, 192.168.1.x) instead of real ones.
- Documentation in English and Czech (DOCS.md, README.md, CHANGELOG.md); configuration options translated (`translations/en.yaml`, `cs.yaml`).
---
- Interval obnovování stránky jde nastavit: `refresh_interval` v konfiguraci add-onu (0 = vypnuto) a pro každý prohlížeč na stránce Nastavení.
- Výchozí konfigurace a demo data používají ukázková zařízení (AP-Living, AP-Office, 192.168.1.x) místo skutečných.
- Dokumentace anglicky a česky (DOCS.md, README.md, CHANGELOG.md), volby konfigurace přeložené (`translations/en.yaml`, `cs.yaml`).

## 0.3.0
- Users and permissions based on the Home Assistant user (Ingress headers): `full_access`, `read_access`, everyone else gets "Access denied". With no roles configured everyone has full access. Without a username from Ingress: read-only. Enforced on the server; actions are logged with the user name. `panel_admin: false`.
- Fix: radio band for APs with the older `hwmode` (11g/11a) is shown as 2.4/5 GHz.
---
- Oprávnění podle uživatele Home Assistantu (hlavičky Ingressu): `full_access`, `read_access`, ostatní „Přístup odepřen“. Bez nastavených rolí má plný přístup každý. Bez jména uživatele z Ingressu jen čtení. Kontrola na serveru, akce se logují se jménem uživatele. `panel_admin: false`.
- Oprava: pásmo rádia u AP se starším `hwmode` (11g/11a) se zobrazuje jako 2,4/5 GHz.

## 0.2.1
- Fix: 0.2.0 did not start, missing dependency `python-multipart`.
---
- Oprava: 0.2.0 nenastartoval, chyběla závislost `python-multipart`.

## 0.2.0
- A tab for each AP in the top bar (online/offline, client count).
- AP page: **Reboot**, **Check for updates**, **Upgrade packages** (`apk`, or `opkg` on older APs); warning when the overlay is almost full.
- New **SSID** page: change name, password, hidden and on/off on several APs at once, with a preview of the `uci` commands; the password is never shown. SSIDs are read from `uci show wireless`, so disabled ones are visible too.
- Actions only from the add-on UI (CSRF protection), one action per AP at a time.
---
- Karta pro každý AP v horní liště (online/offline, počet klientů).
- Stránka AP: **Restart**, **Zkontrolovat aktualizace**, **Upgrade balíčků** (`apk`, na starších AP `opkg`), varování při plném overlay.
- Nová stránka **SSID**: změna názvu, hesla, skrytí a zapnutí na více AP najednou s náhledem příkazů `uci`, heslo se nezobrazuje. SSID se čtou z `uci show wireless`, takže jsou vidět i vypnutá.
- Akce jen z rozhraní add-onu (ochrana proti CSRF), na jednom AP jen jedna akce naráz.

## 0.1.3
- Client band (2.4/5/6 GHz) and negotiated standard (Wi-Fi 4/5/6/7, 802.11a/b/g); filter by band.
---
- U klientů pásmo (2,4/5/6 GHz) a vyjednaný standard (Wi-Fi 4/5/6/7, 802.11a/b/g), filtr podle pásma.

## 0.1.2
- Renamed to OpenWrt-Admin (slug `openwrt_admin`).
---
- Přejmenováno na OpenWrt-Admin (slug `openwrt_admin`).

## 0.1.1
- Dark mode following the Home Assistant theme, otherwise the system setting.
---
- Tmavý režim podle motivu Home Assistantu, jinak podle systému.

## 0.1.0
- First, read-only version: AP overview, radios, SSIDs, clients, AP details, JSON API, client names/IPs from the router's DHCP, SSH key generated by the add-on, Ingress panel, demo mode.
---
- První verze, jen čtení: přehled AP, rádia, SSID, klienti, detail AP, JSON API, jména a IP klientů z DHCP na routeru, SSH klíč generovaný add-onem, panel přes Ingress, demo režim.
