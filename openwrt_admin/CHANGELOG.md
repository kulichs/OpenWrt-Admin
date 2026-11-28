# Changelog

Each version in English, then Czech. / Každá verze anglicky, pak česky.

## 0.32.3
- **Fix of a wrong tip in 0.32.2:** the DOCS suggested `from="<HA IP>"` in front of the app key in `authorized_keys`. **OpenWrt's dropbear does not support `from=`** and rejects the whole line – the app would no longer log in. If you added it, remove it: `sed -i 's#^from="[^"]*" ##' /etc/dropbear/authorized_keys`.
- **Settings → Limit the app key by IP:** for every device the address the app connects from (`$SSH_CLIENT`, can be several – HA reaches networks from different interfaces), whether the key is limited, a suggested list of allowed addresses and the commands to run on each device: a small script `/etc/dropbear/owadmin-gate.sh` via `command="…"` lets the key in only from the listed addresses (others are logged and refused), plus `no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty`; `/etc/dropbear/` goes to `/etc/sysupgrade.conf`. Undo command included.
- Verified against the dropbear source (supported key options, `command=` parsing without unescaping, `SSH_ORIGINAL_COMMAND`); the script passes every app command unchanged (firewall / DHCP / VLAN reads, safety-net script, exit codes) and refuses others without running them; the suggested commands tested as copied (install, second run changes nothing, undo restores the line).
---
- **Oprava chybného tipu z 0.32.2:** DOCS radily dát před klíč aplikace v `authorized_keys` `from="<IP HA>"`. **Dropbear v OpenWrt `from=` nezná** a odmítne celý řádek – aplikace by se už nepřihlásila. Kdo to tam dal, ať to odstraní: `sed -i 's#^from="[^"]*" ##' /etc/dropbear/authorized_keys`.
- **Nastavení → Omezení klíče aplikace podle IP:** u každého zařízení adresa, ze které se aplikace připojuje (`$SSH_CLIENT`, může jich být víc – HA chodí do sítí z různých rozhraní), jestli je klíč omezený, návrh povolených adres a příkazy pro každé zařízení: malý skript `/etc/dropbear/owadmin-gate.sh` přes `command="…"` pustí klíč jen z povolených adres (ostatní zapíše do logu a odmítne), k tomu `no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty`; `/etc/dropbear/` se přidá do `/etc/sysupgrade.conf`. Včetně příkazu pro zrušení.
- Ověřeno proti zdrojáku dropbearu (podporované volby klíče, `command=` bez odstraňování escapů, `SSH_ORIGINAL_COMMAND`); skript propustí všechny příkazy aplikace beze změny (čtení firewallu / DHCP / VLAN, skript pojistky, návratové kódy) a ostatní odmítne bez spuštění; navržené příkazy vyzkoušené tak, jak se zkopírují (instalace, druhé spuštění nic nezmění, zrušení vrátí řádek).

## 0.32.2
- **Security – SSH host key check:** the fingerprint of each device's SSH key is stored on the first connection (`/data/known_hosts.json`). A changed key means the connection is refused before any command is sent, and a red banner shows the old and new fingerprint with **Accept new key** (full access; expected after reinstall / `sysupgrade -n`). Before, the app connected to anything answering on the device address.
- **Stale preview:** **Apply** first checks on the device that the configuration is still the one the preview was built from (firewall, DHCP, networks, VLANs on APs); if LuCI or someone else changed it meanwhile, nothing is saved and the app asks for a new preview. The fingerprint is read together with the data, no extra connection.
- New-device notifications: device names and places are escaped for Markdown (a name cannot turn into a link or formatting).
- DOCS: tip to limit the app key with `from="<HA IP>"` in `authorized_keys`.
- Verified: fake SSH server with a changed key (no command reaches it, refusal survives a restart, accept), banner and roles, the configuration check in a shell with a fake `uci` (unchanged → saved, changed → STALE and nothing written), parsers unaffected, Markdown escaping.
---
- **Bezpečnost – kontrola klíče SSH zařízení:** otisk klíče SSH každého zařízení se uloží při prvním spojení (`/data/known_hosts.json`). Změněný klíč = spojení se odmítne dřív, než se pošle jakýkoli příkaz, a červený pruh ukáže starý a nový otisk s tlačítkem **Přijmout nový klíč** (plný přístup; po reinstalaci / `sysupgrade -n` je to v pořádku). Dřív se aplikace připojila k čemukoli, co na adrese zařízení odpovědělo.
- **Zastaralý náhled:** **Použít** nejdřív na zařízení ověří, že konfigurace je pořád ta, ze které náhled vznikl (firewall, DHCP, sítě, VLAN na AP); když ji mezitím změnilo LuCI nebo někdo jiný, nic se neuloží a aplikace požádá o nový náhled. Otisk se čte spolu s daty, žádné spojení navíc.
- Upozornění na nová zařízení: jména a místa jsou ošetřená pro Markdown (jméno se nemůže změnit v odkaz ani formátování).
- DOCS: tip omezit klíč aplikace přes `from="<IP HA>"` v `authorized_keys`.
- Ověřeno: falešný SSH server se změněným klíčem (nedostane žádný příkaz, odmítnutí přežije restart, přijetí), pruh a role, kontrola konfigurace v shellu s falešným `uci` (beze změny → uloženo, změna → STALE a nic nezapsáno), parsery beze změny, escapování Markdownu.

## 0.32.1
- **Security – roles only by username:** `full_access` / `read_access` are matched against the HA **username** (or user ID), no longer the display name. Every HA user can change their own display name in the profile, so a read-only user could rename themselves to an admin's display name and get full access. **If you have display names in the lists, replace them with usernames** – the Settings page lists entries that match no HA username and suggests the right one; the "Access denied" page says why.
- **Empty role lists:** full access only for **HA administrators**, other users read-only (before: everyone had full access). The app reads the HA user list (`config/auth/list`) every 5 minutes; if it cannot, non-listed users are read-only.
- **Safety net:** while a change on a device waits for confirmation (up to 90 s), another change, reboot or package upgrade on the same device is refused with the remaining time – a rollback of the first change would otherwise also undo the second one.
- Verified: display-name trick refused, username / user ID / case-insensitive, admin vs. non-admin with empty lists, unavailable user list, Settings warnings, second change during an armed safety net (simulated).
---
- **Bezpečnost – role jen podle uživatelského jména:** `full_access` / `read_access` se porovnávají s **uživatelským jménem** HA (nebo ID uživatele), už ne se zobrazovaným jménem. Zobrazované jméno si v profilu mění každý uživatel sám, takže uživatel jen pro čtení se mohl přejmenovat na zobrazované jméno správce a dostat plný přístup. **Kdo má v seznamech zobrazovaná jména, ať je nahradí uživatelskými** – stránka Nastavení vypíše položky, které neodpovídají žádnému uživatelskému jménu v HA, a navrhne správné; stránka „Přístup odepřen“ řekne proč.
- **Prázdné seznamy rolí:** plný přístup jen **administrátoři HA**, ostatní jen čtení (dřív měl plný přístup každý). Aplikace čte seznam uživatelů HA (`config/auth/list`) každých 5 minut; když nejde načíst, mají neuvedení uživatelé jen čtení.
- **Pojistka:** dokud změna na zařízení čeká na potvrzení (až 90 s), další změna, restart nebo upgrade balíčků na stejném zařízení se odmítne se zbývajícím časem – návrat první změny by jinak vrátil i tu druhou.
- Ověřeno: trik se zobrazovaným jménem odmítnut, uživatelské jméno / ID / velikost písmen, admin a neadmin při prázdných seznamech, nedostupný seznam uživatelů, varování v Nastavení, druhá změna při běžící pojistce (simulace).

## 0.32.0
- **Client history and roaming** (new page History): which AP and band each Wi-Fi client was on, as a timeline per device (colour = AP, taller bar = 5/6 GHz), for 24 h / 7 days / 30 days. Counts of AP roams and **ping-pong** (A → B → A within 10 minutes), share of time per AP, worst signal; a device's detail lists events (connected, AP roam with the signal before/after, band change, disconnected). "history" link at every client in the client tables.
- Stored as sessions in `/data/client_history.sqlite`; the start comes from the client's connected time, so it is exact even with a 30 s read interval. Clients of an AP that could not be read are not ended. New option `client_history_days` (default 30, 0 = off); older records are removed once a day.
- Verified: session tracking (stay, roam, return, AP offline, disconnect, reconnect), persistence across restart, demo with synthetic roaming. Not yet on the real network.
---
- **Historie klientů a roaming** (nová stránka Historie): na kterém AP a pásmu byl každý Wi-Fi klient, jako časová osa po zařízeních (barva = AP, vyšší pruh = 5/6 GHz), za 24 h / 7 dní / 30 dní. Počet přechodů mezi AP a **skákání** (A → B → A do 10 minut), podíl času na AP, nejhorší signál; detail zařízení vypíše události (připojení, přechod AP se signálem před/po, změna pásma, odpojení). Odkaz „historie“ u každého klienta v tabulkách klientů.
- Ukládá se po úsecích do `/data/client_history.sqlite`; začátek se bere z doby připojení klienta, takže je přesný i při čtení jednou za 30 s. Klienti AP, které se nepodařilo přečíst, se neukončují. Nová volba `client_history_days` (výchozí 30, 0 = vypnuto); starší záznamy se jednou denně mažou.
- Ověřeno: sledování úseků (zůstává, přechod, návrat, AP offline, odpojení, nové připojení), uchování přes restart, demo s vymyšleným roamingem. Na skutečné síti zatím ne.

## 0.31.0
- adblock: **scheduled list refresh** on the Services tab – time and days, one tagged line in the router's crontab (`# owadmin adblock-reload`), skipped while adblock is paused from the app; other adblock cron lines (LuCI timer, manual) are shown as set elsewhere. The services read now includes the adblock crontab lines and whether crond runs.
- Verified: crontab commands in a real shell (replace, delete, block lines untouched), parsing of own / foreign / complex lines, demo schedule → cancel. Not yet on the real network.
---
- adblock: **plánované obnovení seznamů** na záložce Služby – čas a dny, jeden označený řádek v crontabu routeru (`# owadmin adblock-reload`), při pozastavení z aplikace se přeskočí; jiné řádky cronu s adblockem (LuCI timer, ručně) se ukážou jako nastavené jinde. Čtení služeb nově bere i řádky crontabu s adblockem a jestli běží crond.
- Ověřeno: příkazy pro crontab ve skutečném shellu (nahrazení, smazání, řádky blokací nedotčené), čtení vlastních / cizích / složitých řádků, demo naplánovat → zrušit. Na skutečné síti zatím ne.

## 0.30.0
- **Firewall rules**: new rule (from zone / IPs / MACs, to router / zone / IPs, protocol, ports, allow / reject / drop), edit, delete and **order** (new rule at the end, at the start or before a chosen rule; ↑ ↓ to move existing ones). Rules with options the form does not know can be toggled, moved and deleted only.
- Checks: opening the router or a zone to the internet and cutting the app's SSH in red; a rule without effect, a rule shadowed by an earlier one with the opposite action, a rule that shadows a later exception, deleting a default OpenWrt rule.
- Verified: "IoT without internet except one device" in both orders (shadowing detected, correct order clean), move, edit, delete, lockout and WAN checks, `uci reorder` semantics in the demo simulator. Not yet on the real network.
---
- **Pravidla firewallu**: nové pravidlo (odkud zóna / IP / MAC, kam router / zóna / IP, protokol, porty, povolit / odmítnout / zahodit), úprava, smazání a **pořadí** (nové na konec, na začátek nebo před zvolené pravidlo; existující ↑ ↓). Pravidla s volbami, které formulář nezná, jde jen zapnout, posunout a smazat.
- Kontroly: otevření routeru nebo zóny z internetu a přerušení SSH aplikace červeně; pravidlo bez účinku, pravidlo zastíněné dřívějším s opačnou akcí, pravidlo, které zastíní pozdější výjimku, smazání výchozího pravidla OpenWrt.
- Ověřeno: „IoT bez internetu kromě jednoho zařízení“ v obou pořadích (zastínění odhaleno, správné pořadí bez varování), posun, úprava, smazání, kontroly odříznutí a WAN, chování `uci reorder` v demo simulátoru. Na skutečné síti zatím ne.

## 0.29.1
- Firewall tab: the intro text no longer says that only turning rules on and off is possible (blocking, port forwards and the zone map have been there since 0.22–0.24).
---
- Záložka Firewall: úvodní text už netvrdí, že jde jen zapínat a vypínat pravidla (blokování, přesměrování portů a mapa zón jsou tam od 0.22–0.24).

## 0.29.0
- **SSID by VLAN**: the network of a new SSID is chosen by VLAN, each AP uses its own network name (HOST / host); radios of APs without that network are disabled, the preview refuses them.
- **Move an SSID to another network** (SSID page, Network: unchanged / VLAN …), with a warning that clients reconnect into the new network.
- VLAN page: **📶 add SSID** at AP networks without an SSID, opens the SSID form with the VLAN preselected. Completes the chain switch → router → APs → Wi-Fi.
- Verified: demo chain VLAN on an AP → new SSID in it → moving an existing SSID (different network names per AP), regression tests. Not yet on the real network.
---
- **SSID podle VLAN**: síť nového SSID se vybírá podle VLAN, každé AP použije své jméno sítě (HOST / host); rádia AP bez té sítě nejdou vybrat, náhled je odmítne.
- **Přesun SSID do jiné sítě** (stránka SSID, Síť: beze změny / VLAN …), s upozorněním, že se klienti připojí už do nové sítě.
- Stránka VLAN: **📶 přidat SSID** u sítí na AP bez SSID, otevře formulář SSID s předvybranou VLAN. Tím je hotový řetězec switch → router → AP → Wi-Fi.
- Ověřeno: demo řetězec VLAN na AP → nové SSID v ní → přesun existujícího SSID (různá jména sítí na AP), regresní testy. Na skutečné síti zatím ne.

## 0.28.0
- **VLAN on the APs** from the VLAN page: + add (one AP) or + to all APs (row). The app creates the VLAN by the AP's method (swconfig switch_vlan, DSA bridge-vlan, or an 8021q device), a bridge and a network without an address (`proto none`), named after the router's network. Each AP separately with the safety net, stops after the first failure.
- Remove a VLAN network from an AP (✕) with its bridge and VLAN – not the native or management network, not with an SSID connected.
- Preview warnings: the switch port to the AP does not carry the VLAN yet, the AP's network reloads, no SSID yet.
- Verified: commands on swconfig, 8021q and DSA APs (including layouts like real APs with existing VLANs and name clashes), demo add to all APs → remove. Not yet on the real network.
---
- **VLAN na AP** ze stránky VLAN: + přidat (jedno AP) nebo + na všechna AP (řádek). Aplikace založí VLAN podle způsobu AP (swconfig switch_vlan, DSA bridge-vlan, nebo zařízení 8021q), most a síť bez adresy (`proto none`) se jménem podle routeru. Každé AP zvlášť s pojistkou, po první chybě se končí.
- Odebrání sítě VLAN z AP (✕) i s mostem a VLAN – ne nativní a správcovská síť, ne s připojeným SSID.
- Upozornění v náhledu: port switche k AP VLAN ještě nemá, síť AP se znovu načte, zatím bez SSID.
- Ověřeno: příkazy na AP se swconfigem, 8021q i DSA (i rozložení jako u skutečných AP se stávajícími VLAN a kolizí jmen), demo přidat na všechna AP → odebrat. Na skutečné síti zatím ne.

## 0.27.0
- VLAN page: **managed switch column** (SNMP, Q-BRIDGE-MIB) – per VLAN the switch ports tagged (T) / untagged (U) with the device on each port, the native VLAN, free ports only as a count. PVID (`dot1qPvid`) is now read too.
- Switch checks: the port to an AP or the router missing a VLAN the device uses, untagged instead of tagged, a different native VLAN; a VLAN not on the port to the router; several untagged VLANs on a port; PVID different from the untagged VLAN; extra VLANs (warning); device port not found. Ports are matched by the device MAC in the switch table or by the port description.
- Verified: switch layouts like a real 24-port switch (all consistent; and with deliberate errors: missing VLAN, untagged instead of tagged, wrong PVID), demo cs + en (one deliberate missing VLAN on the demo switch). Not yet on the real network.
---
- Stránka VLAN: **sloupec spravovaného switche** (SNMP, Q-BRIDGE-MIB) – u každé VLAN porty switche tagovaně (T) / netagovaně (U) se zařízením na portu, nativní VLAN, volné porty jen jako počet. Nově se čte i PVID (`dot1qPvid`).
- Kontroly switche: port k AP nebo routeru nemá VLAN, kterou zařízení používá, má ji netagovaně místo tagovaně, jinou nativní VLAN; VLAN chybí na portu k routeru; víc netagovaných VLAN na portu; PVID jiné než netagovaná VLAN; VLAN navíc (upozornění); port zařízení nenalezen. Porty se párují podle MAC zařízení v tabulce switche nebo podle popisu portu.
- Ověřeno: rozložení jako u skutečného 24portového switche (vše sedí; a s úmyslnými chybami: chybějící VLAN, netagovaně místo tagovaně, špatné PVID), demo cs + en (na demo switchi jedna úmyslně chybějící VLAN). Na skutečné síti zatím ne.

## 0.26.0
- **New network on the router** (VLAN page, full access): name, VLAN ID and subnet suggestions, VLAN method by the router (DSA bridge-vlan, swconfig switch_vlan, or an 8021q device on a port), DHCP range, firewall zone (new isolated zone with optional internet and DHCP/DNS rules, an existing zone, or none). One preview, saved at once with the safety net (network + dhcp + firewall, fw4 check).
- **Delete a network** (✕ in the router column): cleans up its bridge, VLAN device, switch/bridge VLAN, DHCP section and a zone used only by it (with forwardings and rules). Not for lan, wan, the management network or non-IP interfaces; red warning when APs still use the VLAN.
- Verified: commands for DSA, swconfig and an 8021q router, error checks (name, VLAN, overlap, range, zone), cleanup on delete, demo create → delete. Not yet on the real network.
---
- **Nová síť na routeru** (stránka VLAN, plný přístup): návrh VLAN ID a podsítě, způsob VLAN podle routeru (DSA bridge-vlan, swconfig switch_vlan, nebo zařízení 8021q na portu), rozsah DHCP, zóna firewallu (nová izolovaná s volitelným internetem a pravidly pro DHCP/DNS, existující, nebo žádná). Jeden náhled, uloží se najednou s pojistkou (network + dhcp + firewall, fw4 check).
- **Smazání sítě** (✕ ve sloupci routeru): uklidí most, VLAN zařízení, VLAN na switchi/mostu, sekci DHCP a zónu, kterou používala jen ona (i s průchody a pravidly). Ne u lan, wan, správcovské sítě a rozhraní bez IP; červené upozornění, když VLAN ještě používají AP.
- Ověřeno: příkazy pro router s DSA, swconfigem i 8021q, kontroly (název, VLAN, překryv, rozsah, zóna), úklid při mazání, demo založit → smazat. Na skutečné síti zatím ne.

## 0.25.0
- New **VLAN** page: networks and SSIDs of the router and all APs in one table (read-only), rows by how traffic goes over the cable to the router (VLAN number or untagged), so differently named networks on different devices line up. Each cell shows the network and its address, SSIDs with bands, ports and the bridge.
  - Understands swconfig (switch_vlan), DSA (bridge-vlan), VLAN on a port without a switch (eth1.N) and the old 19.07 style.
  - Checks: a VLAN device in two bridges, a VLAN missing on the device's switch, SSID → missing network, the same SSID in different networks or VLANs, a VLAN used by an AP but missing on the router; warnings for an AP address in a non-management network, several default gateways, a bare CPU port in a bridge, an address outside the router's subnet.
  - Keys and passwords are filtered out on the device (POSIX `grep -v -e`), they never reach the app.
- Verified: parsing of swconfig, DSA, 8021q and 19.07 configurations (including real-world-like layouts with a shared native VLAN and an AP with an IP in a guest network), secret filtering, demo cs + en. Not yet on the real network.
---
- Nová stránka **VLAN**: sítě a SSID routeru a všech AP v jedné tabulce (jen čtení), řádky podle toho, jak jde provoz po kabelu k routeru (číslo VLAN nebo bez tagu), takže se srovnají i různě pojmenované sítě na různých zařízeních. V buňce je síť a její adresa, SSID s pásmy, porty a most.
  - Rozumí swconfigu (switch_vlan), DSA (bridge-vlan), VLAN na portu bez switche (eth1.N) i starému zápisu 19.07.
  - Kontroly: VLAN zařízení ve dvou mostech, VLAN chybí na switchi zařízení, SSID → neexistující síť, stejné SSID v různých sítích nebo VLAN, VLAN použitá na AP, ale chybí na routeru; upozornění na adresu AP v nesprávcovské síti, víc výchozích bran, holý CPU port v mostu, adresu mimo síť routeru.
  - Klíče a hesla se odfiltrují už na zařízení (POSIX `grep -v -e`), do aplikace nedorazí.
- Ověřeno: zpracování konfigurací swconfig, DSA, 8021q a 19.07 (i rozložení podobná skutečným – sdílená nativní VLAN, AP s adresou v hostovské síti), filtrování tajných údajů, demo cs + en. Na skutečné síti zatím ne.

## 0.24.0
- **Zone map editable by clicking** (Firewall tab, full access): a click switches a cell, changed cells are dashed and can be undone, "Preview changes" saves all of them at once with the safety net. Router column = zone input ACCEPT/REJECT, diagonal = forward inside the zone, other cells = `forwarding` sections (new ones named `owa_zf_<id>`, a disabled one is turned on, removing deletes the enabled ones).
  - Red preview: opening the router, a zone or the zone itself to traffic from the internet; closing input from the zone the app connects from without a rule for its SSH port.
  - Warnings: a whole zone gets access to another, a zone loses internet access, closing input without DHCP/DNS rules, rule exceptions that still apply, a zone mixing WAN and other networks, a zone without an interface.
- Verified: change parsing and commands, risks on the demo and a synthetic router with WAN in a shared zone, demo cs + en (select, undo, preview, apply). Not yet on the real network.
---
- **Mapa zón jde upravovat klikáním** (záložka Firewall, plný přístup): kliknutí políčko přepne, změněná jsou čárkovaná a jde je vrátit, „Náhled změn“ uloží všechny najednou s pojistkou. Sloupec router = input zóny ACCEPT/REJECT, úhlopříčka = forward uvnitř zóny, ostatní políčka = sekce `forwarding` (nové pojmenované `owa_zf_<id>`, vypnutá se zapne, odebrání smaže zapnuté).
  - Červený náhled: otevření routeru, zóny nebo zóny samotné provozu z internetu; zavření vstupu ze zóny, odkud se připojuje aplikace, bez pravidla pro její port SSH.
  - Upozornění: celá zóna dostane přístup do jiné, zóna přijde o internet, zavření vstupu bez pravidel pro DHCP/DNS, výjimky z pravidel, které platí dál, zóna s WAN i dalšími sítěmi, zóna bez rozhraní.
- Ověřeno: zpracování změn a příkazy, rizika na demu a na syntetickém routeru s WAN ve sdílené zóně, demo cs + en (označit, vrátit, náhled, použít). Na skutečné síti zatím ne.

## 0.23.0
- **Port forwards** on the Firewall tab: new, edit, delete (also forwards created in LuCI). Protocol, port or range from the internet, destination IP (suggestions from DHCP), inside port, optional "Only from" (IP or CIDR). The destination zone is found from the IP; new forwards are stored in a named section `firewall.owa_fwd_<id>`.
  - Errors: invalid port/IP, IP outside the router's networks or on WAN, ranges of different size, the port already forwarded elsewhere.
  - Warnings: the router itself listens on the port, often attacked ports without "Only from", a large range, an IP without a static lease, the router's own address.
- **Protection against cutting off management**: the app reads where it connects from (`$SSH_CLIENT`) and shows a red preview when a change would most likely cut its SSH (turning off an ACCEPT rule for the SSH port, turning on a blocking rule, a forward on the SSH port, blocking the Home Assistant machine). The safety net still reverts the router after 90 s if the app cannot confirm.
- The firewall read also takes the router's IPv4 addresses (`ip -4 addr`).
- Verified: checks and commands (add, edit, delete, duplicates, ranges), lockout detection on synthetic routers (LAN input REJECT with an SSH rule, SSH on another port from another zone, an old router without `$SSH_CLIENT`), demo cs + en. Not yet on the real network.
---
- **Přesměrování portů** na záložce Firewall: nové, úprava, smazání (i přesměrování vytvořená v LuCI). Protokol, port nebo rozsah z internetu, cílová IP (nabídka z DHCP), port uvnitř, volitelně „Jen z adresy“ (IP nebo CIDR). Cílová zóna se určí podle IP; nová přesměrování se ukládají do pojmenované sekce `firewall.owa_fwd_<id>`.
  - Chyby: neplatný port/IP, IP mimo sítě routeru nebo ve WAN, různě velké rozsahy, port už přesměrovaný jinam.
  - Upozornění: na portu poslouchá router, často napadané porty bez „Jen z adresy“, velký rozsah, IP bez statické zápůjčky, adresa samotného routeru.
- **Ochrana proti odříznutí správy**: aplikace si přečte, odkud se připojuje (`$SSH_CLIENT`), a když by změna nejspíš přerušila její SSH (vypnutí pravidla ACCEPT pro port SSH, zapnutí blokujícího pravidla, přesměrování portu SSH, blokace počítače s Home Assistantem), ukáže náhled červeně. Pojistka router dál po 90 s vrátí, když aplikace změnu nepotvrdí.
- Čtení firewallu bere i IPv4 adresy routeru (`ip -4 addr`).
- Ověřeno: kontroly a příkazy (nové, úprava, smazání, duplicity, rozsahy), detekce odříznutí na syntetických routerech (LAN input REJECT s pravidlem pro SSH, SSH na jiném portu z jiné zóny, starý router bez `$SSH_CLIENT`), demo cs + en. Na skutečné síti zatím ne.

## 0.22.1
- The change preview opens directly below the button, form or table row it was requested from (services, DHCP, DNS, firewall, radios, SSIDs), not at the top of the panel. Opening another preview closes the previous one; the page scrolls to it if needed.
---
- Náhled změn se otevře hned pod tlačítkem, formulářem nebo řádkem tabulky, odkud byl vyžádán (služby, DHCP, DNS, firewall, rádia, SSID), ne nahoře v panelu. Otevření dalšího náhledu ten předchozí zavře; stránka k němu případně odroluje.

## 0.22.0
- **Blocking devices** from the internet (Firewall tab → Blocked devices, or "⛔ internet" next to a client in the client tables):
  - **now for** 15 min – 24 h, **until** a date and time, **schedule** (from–to, days of the week; across midnight the next morning) or **permanently**; one or more MACs, a label.
  - Firewall rule in a named section `firewall.owa_blk_<id>` (`src='*'`, `dest=<WAN zone>`, `src_mac`, REJECT). Time is handled by **cron on the router** in its local time – a schedule turns the rule on and off, a temporary block deletes itself (rule and cron line) at the given minute; it keeps working while Home Assistant is off and after a router reboot. fw4's own time options are not used (UTC vs local time is unclear and ranges across midnight are not handled).
  - List of blocks with the router time, mode, remaining time, state now; "Unblock" deletes the rule and its cron lines. Other blocking rules (by MAC/IP, not created by the app) are listed too and can be turned on/off; a port-limited rule says so.
  - Warnings: the device is already blocked, a random (private) MAC, a temporary block whose end passed while the router was off.
  - Saved with the safety net, which now backs up and restores the crontab too.
- Verified: schedule and window logic incl. midnight, cron lines executed against a test crontab (a temporary block removes only its own line), safety net with crontab (existing / missing, confirmed / not confirmed), demo cs + en (client link, temporary, schedule, unblock), real router rules rendered. Not yet on the real network.
---
- **Blokování zařízení** do internetu (záložka Firewall → Blokovaná zařízení, nebo „⛔ internet“ u klienta v tabulkách klientů):
  - **teď na** 15 min – 24 h, **do** data a času, **rozvrh** (od–do, dny v týdnu; přes půlnoc ráno dalšího dne) nebo **trvale**; jedna i více MAC, popis.
  - Pravidlo firewallu v pojmenované sekci `firewall.owa_blk_<id>` (`src='*'`, `dest=<zóna WAN>`, `src_mac`, REJECT). Čas hlídá **cron na routeru** v jeho místním čase – rozvrh pravidlo zapíná a vypíná, dočasná blokace se v danou minutu sama smaže (pravidlo i řádek cronu); funguje i když je Home Assistant vypnutý a po restartu routeru. Časové volby fw4 se nepoužívají (není jisté UTC vs. místní čas a rozsahy přes půlnoc fw4 neřeší).
  - Seznam blokací s časem routeru, režimem, zbývajícím časem a stavem teď; „Odblokovat“ smaže pravidlo i jeho řádky cronu. Ostatní blokovací pravidla (podle MAC/IP, nevytvořená aplikací) jsou v seznamu taky a jde je zapnout/vypnout; pravidlo jen na některé porty to uvádí.
  - Upozornění: zařízení už je blokované, náhodná (soukromá) MAC, dočasná blokace, jejíž konec proběhl při vypnutém routeru.
  - Ukládá se s pojistkou, která teď zálohuje a vrací i crontab.
- Ověřeno: logika rozvrhu a okna vč. přes půlnoc, řádky cronu spuštěné nad testovacím crontabem (dočasná blokace smaže jen svůj řádek), pojistka s crontabem (existuje / neexistuje, potvrzeno / nepotvrzeno), demo cs + en (odkaz od klienta, dočasná, rozvrh, odblokování), vykreslení skutečných pravidel routeru. Na skutečné síti zatím ne.

## 0.21.1
- Firewall works on other routers too: the WAN interface is found by the default route (`ip route`), so it may be called anything (`wan`, `wwan`, `pppoe-wan`…); fallback by name and by the masquerading zone. Listening services from `netstat` or `ss`. On fw3 (OpenWrt 21.02 and older) the tab shows a limited read-only overview with an explanation and does not offer changes.
- DHCP: the lease file is taken from the dnsmasq configuration (`leasefile`), default `/tmp/dhcp.leases`.
- Demo data no longer contain process IDs copied from a real router.
---
- Firewall funguje i na jiných routerech: rozhraní WAN se hledá podle výchozí cesty (`ip route`), takže se může jmenovat jakkoli (`wan`, `wwan`, `pppoe-wan`…); záložně podle jména a podle zóny s maškarádou. Poslouchající služby z `netstat` nebo `ss`. Na fw3 (OpenWrt 21.02 a starší) ukáže záložka omezený přehled jen pro čtení s vysvětlením a změny nenabízí.
- DHCP: soubor se zápůjčkami se bere z konfigurace dnsmasq (`leasefile`), výchozí `/tmp/dhcp.leases`.
- Demo data už neobsahují čísla procesů zkopírovaná ze skutečného routeru.

## 0.21.0
- New **Firewall** tab on the router page (Overview | Services | DHCP | DNS | Firewall), fw4:
  - **Checks**: the WAN interface in a zone with input ACCEPT (lists the router services reachable from the internet), WAN zone with forward ACCEPT, a network or interface in several zones (only the first one applies – taken from the real order in `fw4 print`), WAN without masquerading, old fw3 includes that fw4 ignores.
  - **Open from the internet**: port forwards, ACCEPT rules from WAN and router services (`netstat`) with their state – open, allowed by a rule, blocked by a rule, port forwarded elsewhere, closed by the zone policy.
  - **Zone map**: matrix "who may go where" (input to the router and forwarding between zones) with the number of rule exceptions.
  - Lists of **port forwards** and **rules** (zones, ports, IP/MAC, time conditions, block tag), custom files in `/etc/nftables.d/`.
  - Existing rules and port forwards can be **turned on and off**.
- New **safety net for saving** (as in LuCI): the device backs up the affected configuration, starts a countdown, applies the change in the background (survives a dropped SSH session) and runs a check (`fw4 check`; on failure the backup is restored immediately). The app reconnects over a new SSH connection and confirms; without confirmation the device restores the backup and reloads after 90 s. Used for the firewall now, later for interfaces and VLANs.
- Verified: parser on a real router configuration (all checks fired correctly: zones, open services, networks in several zones, blocking rules), safety net simulated (confirmed / not confirmed / failed command), demo cs + en, read-only user. Not yet on the real network.
---
- Nová záložka **Firewall** na stránce routeru (Přehled | Služby | DHCP | DNS | Firewall), fw4:
  - **Kontroly**: rozhraní WAN v zóně se vstupem ACCEPT (vypíše služby routeru dostupné z internetu), zóna WAN s průchodem ACCEPT, síť nebo rozhraní ve více zónách (platí jen první – podle skutečného pořadí z `fw4 print`), WAN bez maškarády, staré include z fw3, které fw4 ignoruje.
  - **Co je otevřené z internetu**: přesměrování portů, pravidla ACCEPT z WAN a služby routeru (`netstat`) se stavem – otevřená, povolená pravidlem, blokovaná pravidlem, port přesměrovaný jinam, zavřená politikou zóny.
  - **Mapa zón**: matice „kdo kam smí“ (vstup na router a průchod mezi zónami) s počtem výjimek z pravidel.
  - Seznam **přesměrování portů** a **pravidel** (zóny, porty, IP/MAC, časové podmínky, štítek blokace), vlastní soubory v `/etc/nftables.d/`.
  - Existující pravidla a přesměrování jde **zapnout a vypnout**.
- Nová **pojistka při ukládání** (jako v LuCI): zařízení si zazálohuje dotčenou konfiguraci, spustí odpočet, změnu provede na pozadí (přežije odpojení SSH) a zkontroluje (`fw4 check`; při chybě hned vrátí zálohu). Aplikace se znovu připojí novým SSH spojením a potvrdí; bez potvrzení zařízení po 90 s vrátí zálohu a znovu načte. Teď pro firewall, později pro rozhraní a VLAN.
- Ověřeno: parser na skutečné konfiguraci routeru (všechny kontroly zafungovaly: zóny, otevřené služby, sítě ve více zónách, blokovací pravidla), pojistka nasimulovaná (potvrzeno / nepotvrzeno / chyba v příkazu), demo cs + en, uživatel jen pro čtení. Na skutečné síti zatím ne.

## 0.20.1
- DNS tab – **upstream servers** can now be changed in two ways:
  - **In dnsmasq (recommended)**: `server` list with `noresolv=1`, takes precedence over the WAN servers, changed with a dnsmasq reload only (no internet outage); empty = back to the WAN servers. Presets CZ.NIC ODVR, Cloudflare, Quad9, Google. **Forwarded domains** (`server=/domain/ip`) can be added and removed.
  - **On the WAN interface** (`network.wan.dns`, `peerdns=0` for DHCP/PPPoE) – in a red section with a warning: a network reload, the internet may drop for a few seconds. The script runs in the background on the router so that a dropped SSH session cannot interrupt it.
  - Before saving the router asks every new server (`nslookup nic.cz <server>`); non-answering servers are flagged, and if none answers the change is refused. After saving the router resolves `nic.cz` through itself; if that fails it **restores the previous settings** (servers, `noresolv`, `peerdns`) on its own.
- Verified: script syntax, the success and rollback path simulated with fake uci/nslookup, demo cs + en (presets, dnsmasq, forwards, WAN, back to WAN, non-answering server, read-only user). Not yet on the real network.
---
- Záložka DNS – **nadřazené servery** jde nově měnit dvěma způsoby:
  - **V dnsmasq (doporučeno)**: seznam `server` s `noresolv=1`, má přednost před servery z WAN, mění se jen reloadem dnsmasq (bez výpadku internetu); prázdné = zpět na servery z WAN. Předvolby CZ.NIC ODVR, Cloudflare, Quad9, Google. **Přesměrované domény** (`server=/doména/ip`) jde přidat a smazat.
  - **Na rozhraní WAN** (`network.wan.dns`, u DHCP/PPPoE `peerdns=0`) – v červené sekci s upozorněním: reload sítě, internet může na pár sekund vypadnout. Skript běží na routeru na pozadí, aby ho nepřerušilo případné odpojení SSH.
  - Před uložením se router zeptá každého nového serveru (`nslookup nic.cz <server>`); neodpovídající označí, a když neodpoví žádný, změnu nepovolí. Po uložení router přeloží `nic.cz` přes sebe; když to nejde, sám **vrátí původní nastavení** (servery, `noresolv`, `peerdns`).
- Ověřeno: syntaxe skriptů, cesta úspěchu i návratu nasimulovaná s falešným uci/nslookup, demo cs + en (předvolby, dnsmasq, přesměrování, WAN, zpět na WAN, neodpovídající server, uživatel jen pro čtení). Na skutečné síti zatím ne.

## 0.20.0
- New **DNS** tab on the router page (Overview | Services | DHCP | DNS), dnsmasq:
  - **Lookup test**: asks dnsmasq on the router (`nslookup <name> 127.0.0.1`) like clients do – shows the address, the alias chain (alias → target → IP), "blocked" for 0.0.0.0 (adblock) or NXDOMAIN; raw output on click. Read-only, allowed for read-only users.
  - **Local names** (`dhcp.@domain`, "Hostnames" in LuCI): add, edit, delete, search. Bare names, own domains (`server.home`) and public domains pointed to an internal address are all allowed. Checks: valid name and IPv4/IPv6, the same name+IP twice, a name that is already an alias; warnings for a second address of the same name and an address outside the router networks.
  - **Aliases** (`dhcp.@cname`): add, edit, delete; warning (and "?" in the list) when dnsmasq does not know the target – an alias only works to a local name or a lease.
  - **DNS settings** read-only: upstream servers (`resolv.conf.auto`, `server`), forwarded domains (`server=/…/`), local domain, names from static leases, rebind protection. Changing upstream DNS restarts WAN, so it stays in LuCI.
  - Changes go the same way as DHCP: preview → Apply → `uci commit dhcp` + `dnsmasq reload`, `uci revert` on error, backup before the first change of the day, syslog entry with the HA user.
- Verified: parser and checks on a real router configuration (14 local names, duplicate detection), lookup parser on real BusyBox output, shell syntax, demo cs + en (lookup incl. alias and adblock, add/edit/delete, read-only user). Not yet on the real network.
---
- Nová záložka **DNS** na stránce routeru (Přehled | Služby | DHCP | DNS), dnsmasq:
  - **Test překladu**: zeptá se dnsmasq na routeru (`nslookup <jméno> 127.0.0.1`) stejně jako klienti – ukáže adresu, řetěz aliasu (alias → cíl → IP), „blokováno“ u 0.0.0.0 (adblock) nebo NXDOMAIN; po kliknutí celý výstup. Jen čtení, smí i uživatel jen pro čtení.
  - **Místní jména** (`dhcp.@domain`, v LuCI „Hostnames“): přidat, upravit, smazat, hledat. Jména bez domény, vlastní domény (`server.home`) i veřejné domény přesměrované na vnitřní adresu. Kontroly: platné jméno a IPv4/IPv6, stejné jméno+IP dvakrát, jméno, které už je alias; varování u druhé adresy stejného jména a u adresy mimo sítě routeru.
  - **Aliasy** (`dhcp.@cname`): přidat, upravit, smazat; varování (a „?“ v seznamu), když dnsmasq cíl nezná – alias funguje jen na místní jméno nebo zápůjčku.
  - **Nastavení DNS** jen pro čtení: nadřazené servery (`resolv.conf.auto`, `server`), přesměrované domény (`server=/…/`), místní doména, jména ze statických zápůjček, ochrana proti rebind. Změna nadřazeného DNS restartuje WAN, proto zůstává v LuCI.
  - Změny jdou stejně jako u DHCP: náhled → Použít → `uci commit dhcp` + `dnsmasq reload`, při chybě `uci revert`, záloha před první změnou dne, záznam do syslogu se jménem uživatele HA.
- Ověřeno: parser a kontroly na skutečné konfiguraci routeru (14 místních jmen, odhalení duplicity), parser překladu na skutečném výstupu BusyBox, syntaxe v shellu, demo cs + en (překlad vč. aliasu a adblocku, přidání/úprava/smazání, uživatel jen pro čtení). Na skutečné síti zatím ne.

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
