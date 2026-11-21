# Changelog

Each version in English, then Czech. / Každá verze anglicky, pak česky.

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
