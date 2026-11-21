# OpenWrt-Admin

**[English](#english) · [Česky](#česky)**

---

## English

Manage OpenWrt access points from Home Assistant: AP status (uptime, load, RAM, free overlay space), radios, SSIDs and all connected Wi-Fi clients with names and IPs from the router's DHCP. Reboot APs, update packages and change SSIDs on several APs at once. Nothing is installed on the APs, everything goes over SSH (`ubus`, `uci`).

The web UI is available in English and Czech (see [Languages](#languages)).

### Installation

**From a Git repository (recommended, Home Assistant offers updates by itself)**

1. **Settings → Apps (Add-ons) → store → ⋮ → Repositories**, add the repository URL, e.g. `https://git.example.com/you/OpenWrt-Admin`.
   - Or in the HA terminal: `ha store add <URL>` (gives a more precise error message).
   - Private repository: `https://user:TOKEN@git.example.com/you/OpenWrt-Admin` (read-only token).
   - Do **not** use *Settings → Marketplace → Add from link*: since HA 2026.11 it only accepts public GitHub/HACS repositories and cannot install add-ons ("Unable to load GitHub repository").
2. **⋮ → Check for updates**, install **OpenWrt-Admin** (the image is built on your device, a few minutes).
3. Set up the **Configuration** tab (below), **Start**, enable **Show in sidebar**.

**As a local add-on:** copy the `openwrt_admin` folder to `/addons/` (Samba share or SSH), then **⋮ → Check for updates**, it appears under **Local add-ons**. Updates: copy again and **Rebuild**.

### Configuration

```yaml
ssh_user: root
poll_interval: 30            # s, 10–600: how often data is collected from the APs
refresh_interval: 30         # s, 0 = off: how often open pages reload (each browser can override it)
language: auto               # auto | en | cs – web UI language (auto = HA user's language)
demo: false                  # true = made-up data, no SSH
full_access:                 # HA usernames with full access (reboot, packages, SSIDs)
  - alice
read_access:                 # HA usernames with read-only access
  - bob
aps:                         # all managed devices – the add-on uses only these
  - name: Router
    host: 192.168.1.1
    type: router             # one router: interfaces, DHCP leases, client names/IPs
  - name: AP-Living
    host: 192.168.1.2
    type: ap                 # default
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # optional
```

`dhcp_router` and `router_name` from versions up to 0.7.0 still work but are deprecated: if `dhcp_router` is set and no device has `type: router`, the device with the same IP becomes the router (so it is never shown twice); otherwise a router is created from `dhcp_router`. Recommended: remove `dhcp_router` and set `type: router` on the router.

### SSH key

On first start the add-on generates an `ed25519` key (stored in `/data`, included in HA backups). The public key is shown in the add-on on the **Settings** page. Add it to every AP and to the router:

```sh
echo 'ssh-ed25519 AAAA… ha-openwrt-admin' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

`/etc/dropbear/authorized_keys` survives a normal `sysupgrade`; after `sysupgrade -n` add it again or put it in a first-boot script (uci-defaults).

> The key logs in as `root` and the add-on can reboot APs and change their configuration. Anyone with access to the add-on's `/data` (HA backups) has root on your APs. **Encrypt your HA backups.**

### What it can do

| Action | Where | Command on the AP |
|---|---|---|
| Reboot | AP or router page | `reboot` |
| Check for updates | AP page | `apk update && apk list --upgradable` (older APs: `opkg`) |
| Upgrade packages | AP page | `apk upgrade` (older APs: `opkg upgrade …`) |
| SSID name, password, hidden, on/off | SSID page | `uci set wireless.<section>.… && uci commit wireless && wifi reload` |

- SSID changes edit **existing** SSIDs (`wifi-iface` sections), they do not create new ones. Tick the APs/radios (bands) the change applies to. A preview of the `uci` commands is shown first; the change is written only after confirming. The password is never shown.
- Reboot and upgrade ask for confirmation.
- **Upgrading packages** is generally discouraged on OpenWrt: new versions go to the overlay and small APs (8 MB flash) may run out of space. For bigger changes build a new image and `sysupgrade`.

### Users and permissions

Login is handled by Home Assistant (Ingress); the add-on has no passwords of its own. It assigns a role based on the HA user who opened the panel:

| Role | Who | Can |
|---|---|---|
| full access | in `full_access` | everything: reboot, packages, SSID changes |
| read-only | in `read_access` | overview, clients, SSIDs, AP details – no action buttons |
| no access | in neither list | "Access denied" page |

- Use the HA **username** (Settings → People → Users) or the display name, case-insensitive.
- **Both lists empty:** everyone who opens the panel has full access. The Settings page warns about it.
- **Ingress sends no username:** read-only, never full access.
- Permissions are enforced on the server, not only hidden in the UI. Actions are logged with the user name.
- The panel is visible to all HA users (`panel_admin: false`). Hiding the panel alone would not be enough: HA does not block the ingress URL for regular users.

### Router

The router (the device with `type: router`) is a device of its own, without radios and SSIDs. It has its own tab, card on the overview and page with:
- system info (model, firmware, uptime, load, RAM, overlay),
- **interfaces** from `ubus call network.interface dump`: WAN/LAN/VLANs, state, protocol, IPv4/IPv6, gateway, uptime,
- **DHCP leases**: every device with an IP from the router, Wi-Fi clients with their AP/SSID/band, the rest marked as wired or not on Wi-Fi,
- the same actions as APs: reboot, check for updates, upgrade packages. Rebooting the router takes the whole network and the internet down for a moment.

The add-on's SSH key must be on the router too.

### Network map

The **Map** page draws the network as a tree: Internet → router → access points → SSIDs → **clients**, plus a node for wired/other DHCP clients with those devices under it. Each node links to its page (SSID nodes to the filtered client list); hover over a client for IP, MAC, band and signal.

- **Clients** are shown with name (or MAC) and signal (green ≥ −60 dBm, orange ≥ −72 dBm, red below); wired devices with their IP. The **Clients** toggle switches them off (then SSIDs show only the count).
- **SSIDs without clients are hidden** by default; the **SSIDs without clients** toggle shows them.
- **Zoom and pan:** mouse wheel zooms around the cursor, dragging moves the map, buttons **+ / − / Fit**. The position is kept when the page refreshes itself.
- **APs working as switches:** wired devices plugged into an AP's ports are shown under that AP in a **LAN** group with the port number, and an AP plugged into another AP is drawn under it (elbow line). Source: the switch MAC table on each AP (`swconfig dev switch0 get arl_table`); the uplink is the port where the router's MAC is seen (from the AP's ARP table), the CPU port is the one tagged in all `switch_vlan`s. If the same MAC is seen on several chained APs, it belongs to the last one. Wired devices that are on no AP port stay under the router (**LAN / other**); the router page shows for each DHCP lease whether it is on Wi-Fi, on an AP port, or elsewhere.
- **Last known place:** a switch forgets a MAC after a few minutes without traffic (e.g. a TV that is switched off). The add-on remembers where each wired device was last seen (AP + port, stored in `/data/wired_seen.json`) and keeps it there, greyed out with "last seen …", while it still has a DHCP lease on the router (without a lease for 24 hours). Entries older than 30 days or for removed APs are deleted.
- Works only where the switch can list its MAC table (swconfig chips such as Atheros/Qualcomm AR8xxx, MediaTek). Unmanaged switches in between are not visible: devices behind them appear at the nearest OpenWrt AP.
- Lines to Wi-Fi clients are dotted, wired lines are solid.

- **Line types:** WAN (grey, thick), LAN untagged (thin), LAN + VLANs = trunk (thick; the tagged VLANs are shown as coloured chips in the AP node), Wi-Fi (dotted). Offline devices: red dashed line.
- **Colours = networks:** the untagged LAN and every VLAN have their own colour, the same on the router, APs and SSIDs. The legend lists the VLAN IDs and the interface names used for them on each device.
- **VLAN IDs** come from device names (`eth0.30`, `br-lan.10`), bridge members (`br-host` → `eth0.30`) and `uci show network` (`ifname`/`device`/`ports`, also older OpenWrt). The interface with the device's own IP (or named `lan`) is always the untagged LAN, even if it is `eth0.1` internally. If a VLAN cannot be detected on an AP, a router interface with the same name (case-insensitive) is used. Works with swconfig and DSA.
- On APs only an interface named `wan*` counts as WAN; VLAN interfaces with their own gateway (DHCP or static) are shown as VLANs. On the router, the interface with the default route is the WAN.
- VLANs that exist only in the switch (`switch_vlan` / `bridge-vlan`) without an interface are not shown.
- The map shows **logical** connections: switches and physical ports are not visible from OpenWrt.

### Overview tiles

The first tile shows **devices online**: the router and all configured APs. Hover over it to see the list.

### Page refresh

Open pages reload every `refresh_interval` seconds (0 = off). Each browser can choose its own value on the add-on's **Settings** page (stored in a cookie). The SSID page never reloads by itself, so a half-filled form is not lost.

### What is read from the APs

| Data | Command |
|---|---|
| model, hostname, version | `ubus call system board` |
| uptime, load, RAM | `ubus call system info` |
| overlay | `df -k /overlay` |
| radios, SSID → network/VLAN | `ubus call network.wireless status` |
| SSIDs incl. disabled ones | `uci -q show wireless` (without passwords) |
| clients (signal, rate, traffic) | `ubus call hostapd.<if> get_clients` |
| switch MAC table (wired devices on AP ports) | `swconfig dev <switch> get arl_table`, `/proc/net/arp` |
| interfaces and VLANs (router, APs) | `ubus call network.interface dump`, `ubus call network.device status` |
| client names and IPs (router) | `ubus call luci-rpc getDHCPLeases`, else `/tmp/dhcp.leases` |

Works on APs without LuCI (only `wpad`/`hostapd` and `ubus`, present in every OpenWrt).

### JSON API

`…/api/state` returns the whole state (APs + clients) as JSON.

### Languages

- **Web UI:** English and Czech. The language is chosen in this order: the choice on the add-on's **Settings** page (per browser) → `language` in the configuration (if not `auto`) → the language of the Home Assistant user → the browser's language → English.
- **Configuration** in Home Assistant (option names and descriptions): `translations/en.yaml`, `cs.yaml`; HA picks the language of the user's profile.
- **Documentation:** this file is bilingual (HA shows one `DOCS.md` for all languages).

**Adding a language** (e.g. German):
1. Copy `app/i18n/en.json` to `app/i18n/de.json`, translate the values (keep the keys and `{placeholders}`), set `_language_name` to `Deutsch`. Values that are lists are plural forms.
2. If the plural rule differs from English, add it to `PLURALS` in `app/i18n.py`.
3. Add `de` to `language: list(auto|cs|en|de)?` in `config.yaml` and optionally `translations/de.yaml`.
Missing keys fall back to English.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `PermissionDenied` | Public key missing in `/etc/dropbear/authorized_keys` on that AP. |
| `TimeoutError` / `ConnectionRefused` | AP off, wrong IP, or dropbear not listening on LAN. |
| Clients without name/IP | `dhcp_router` not set, key not on the router, or a static IP. |
| No SSIDs | Wi-Fi disabled on the AP (`ubus list 'hostapd.*'` is empty). |
| "Access denied" | HA user is in neither `full_access` nor `read_access`. |
| Everyone is read-only | Ingress sends no username (Settings shows "Přihlášen jako –"), or the name does not match the HA username. |
| Add-on does not start, `python-multipart` | Old version 0.2.0, update. |
| Repository cannot be added | Wrong place (Marketplace), HA cannot reach the Git server (try the IP), wrong token. Details: Settings → System → Logs → **Supervisor**. |

Logs: the add-on's **Log** tab.

---

## Česky

Správa OpenWrt access pointů z Home Assistantu: stav AP (uptime, load, RAM, volné místo na overlay), rádia, SSID a všichni připojení Wi-Fi klienti se jmény a IP z DHCP na routeru. Restart AP, aktualizace balíčků a změna SSID na více AP najednou. Na AP se nic neinstaluje, vše jde přes SSH (`ubus`, `uci`).

### Instalace

**Z Git repozitáře (doporučeno, HA nabízí aktualizace sám)**

1. **Nastavení → Aplikace (Doplňky) → obchod → ⋮ → Repozitáře**, přidat URL repozitáře, např. `https://git.example.com/ty/OpenWrt-Admin`.
   - Nebo v terminálu HA: `ha store add <URL>` (dá přesnější chybovou hlášku).
   - Soukromé repo: `https://uzivatel:TOKEN@git.example.com/ty/OpenWrt-Admin` (token jen pro čtení).
   - **Ne** přes *Nastavení → Marketplace → Přidat z odkazu*: od HA 2026.11 bere jen veřejné repozitáře z GitHubu/HACS a add-ony neinstaluje („Nelze načíst repozitář GitHub“).
2. **⋮ → Zkontrolovat aktualizace**, nainstalovat **OpenWrt-Admin** (image se staví na zařízení, pár minut).
3. Nastavit kartu **Konfigurace** (níže), **Spustit**, zapnout **Zobrazit v postranním panelu**.

**Jako lokální add-on:** složku `openwrt_admin` zkopírovat do `/addons/` (Samba share nebo SSH), pak **⋮ → Zkontrolovat aktualizace**, objeví se v **Místní doplňky**. Aktualizace: znovu zkopírovat a **Přestavět**.

### Konfigurace

```yaml
ssh_user: root
poll_interval: 30            # s, 10–600: jak často se sbírají data z AP
refresh_interval: 30         # s, 0 = vypnuto: jak často se otevřené stránky obnovují (prohlížeč si může změnit)
language: auto               # auto | en | cs – jazyk webového rozhraní (auto = podle uživatele HA)
demo: false                  # true = vymyšlená data, bez SSH
full_access:                 # uživatelská jména HA s plným přístupem (restart, balíčky, SSID)
  - alice
read_access:                 # uživatelská jména HA jen pro čtení
  - bob
aps:                         # všechna spravovaná zařízení – add-on používá jen tato
  - name: Router
    host: 192.168.1.1
    type: router             # jeden router: rozhraní, DHCP klienti, jména/IP klientů
  - name: AP-Living
    host: 192.168.1.2
    type: ap                 # výchozí
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # volitelné
```

`dhcp_router` a `router_name` z verzí do 0.7.0 fungují dál, ale jsou zastaralé: když je `dhcp_router` vyplněný a žádné zařízení nemá `type: router`, router se stane zařízení se stejnou IP (nikdy se tedy nezobrazí dvakrát); jinak se router vytvoří z `dhcp_router`. Doporučeno: `dhcp_router` smazat a routeru dát `type: router`.

### SSH klíč

Při prvním startu si add-on vygeneruje klíč `ed25519` (uložený v `/data`, je součástí záloh HA). Veřejnou část najdeš v add-onu na stránce **Nastavení**. Přidej ji na každý AP i na router:

```sh
echo 'ssh-ed25519 AAAA… ha-openwrt-admin' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

`/etc/dropbear/authorized_keys` přežije běžný `sysupgrade`. Po `sysupgrade -n` ho přidej znovu, nebo ho dej do first-boot skriptu (uci-defaults).

> Klíč se přihlašuje jako `root` a add-on umí AP restartovat a měnit jeho konfiguraci. Kdo má přístup do `/data` add-onu (zálohy HA), má root na AP. **Zálohy HA šifruj.**

### Co umí

| Akce | Kde | Příkaz na AP |
|---|---|---|
| Restart | stránka AP nebo routeru | `reboot` |
| Zkontrolovat aktualizace | stránka AP | `apk update && apk list --upgradable` (starší AP: `opkg`) |
| Upgrade balíčků | stránka AP | `apk upgrade` (starší AP: `opkg upgrade …`) |
| Název, heslo, skrytí, zapnutí SSID | stránka SSID | `uci set wireless.<sekce>.… && uci commit wireless && wifi reload` |

- Změna SSID upravuje **existující** SSID (sekce `wifi-iface`), nová nepřidává. Zaškrtnutím se vybírají AP a rádia (pásma), kterých se změna týká. Nejdřív se zobrazí náhled příkazů `uci`, zapíše se až po potvrzení. Heslo se nikde nezobrazuje.
- Restart a upgrade se potvrzují.
- **Upgrade balíčků** OpenWrt obecně nedoporučuje: nové verze jdou do overlay a na malých AP (8 MB flash) může místo dojít. Pro větší změny je lepší nový image a `sysupgrade`.

### Uživatelé a oprávnění

Přihlášení řeší Home Assistant (Ingress), add-on vlastní hesla nemá. Podle uživatele HA, který panel otevřel, přidělí roli:

| Role | Kdo | Co smí |
|---|---|---|
| plný přístup | je v `full_access` | vše: restart, balíčky, změny SSID |
| jen čtení | je v `read_access` | přehled, klienti, SSID, detail AP – bez tlačítek akcí |
| bez přístupu | není v žádném seznamu | stránka „Přístup odepřen“ |

- Píše se **uživatelské jméno** HA (Nastavení → Lidé → Uživatelé) nebo zobrazované jméno, velikost písmen nehraje roli.
- **Oba seznamy prázdné:** plný přístup má každý, kdo panel otevře. Stránka Nastavení na to upozorní.
- **Ingress nepošle jméno uživatele:** jen čtení, nikdy plný přístup.
- Kontroluje se na serveru, ne jen v zobrazení. Akce se logují se jménem uživatele.
- Panel vidí všichni uživatelé HA (`panel_admin: false`). Samotné schování panelu by nestačilo, HA ingress adresu u běžného uživatele neblokuje.

### Router

Router (zařízení s `type: router`) je samostatné zařízení, bez rádií a SSID. Má vlastní záložku, kartu na přehledu a stránku s:
- informacemi o systému (model, firmware, uptime, load, RAM, overlay),
- **rozhraními** z `ubus call network.interface dump`: WAN/LAN/VLANy, stav, protokol, IPv4/IPv6, brána, jak dlouho běží,
- **DHCP klienty**: všechna zařízení, která mají IP od routeru; Wi-Fi klienti s AP/SSID/pásmem, ostatní jako kabel nebo nepřipojeno k Wi-Fi,
- stejnými akcemi jako AP: restart, kontrola aktualizací, upgrade balíčků. Restart routeru na chvíli odpojí celou síť i internet.

SSH klíč add-onu musí být i na routeru.

### Mapa sítě

Stránka **Mapa** kreslí síť jako strom: Internet → router → access pointy → SSID → **klienti**, k tomu uzel pro DHCP klienty na kabelu a ostatní se zařízeními pod ním. Každý uzel je odkaz na svou stránku (SSID na vyfiltrovaný seznam klientů); po najetí na klienta se ukáže IP, MAC, pásmo a signál.

- **Klienti** jsou se jménem (nebo MAC) a signálem (zeleně ≥ −60 dBm, oranžově ≥ −72 dBm, červeně slabší); zařízení na kabelu s IP. Přepínač **Klienti** je vypne (SSID pak ukazují jen počet).
- **SSID bez klientů se skrývají**, přepínač **SSID bez klientů** je zobrazí.
- **Zoom a posun:** kolečko myši přibližuje kolem kurzoru, tažením se mapa posouvá, tlačítka **+ / − / Vše**. Pozice zůstane i po automatickém obnovení stránky.
- **AP jako switch:** zařízení zapojená kabelem do portů AP se ukážou pod tím AP ve skupině **LAN** s číslem portu a AP zapojený přes jiný AP se nakreslí pod ním (lomená čára). Zdroj: tabulka MAC adres switche na každém AP (`swconfig dev switch0 get arl_table`); uplink je port, na kterém je MAC routeru (z ARP tabulky AP), CPU port je ten tagovaný ve všech `switch_vlan`. Když stejnou MAC vidí víc AP zapojených za sebou, patří poslednímu. Kabelová zařízení, která nejsou na portu žádného AP, zůstávají u routeru (**LAN / ostatní**); stránka routeru u každého DHCP klienta ukazuje, jestli je na Wi-Fi, na portu AP, nebo jinde.
- **Poslední známé místo:** switch MAC po pár minutách bez provozu zapomene (např. vypnutá televize). Add-on si pamatuje, kde bylo každé kabelové zařízení naposledy vidět (AP + port, v `/data/wired_seen.json`), a nechá ho tam šedě s „naposledy viděno …“, dokud má na routeru DHCP záznam (bez záznamu 24 hodin). Záznamy starší než 30 dní nebo pro odebrané AP se mažou.
- Funguje jen tam, kde switch umí vypsat tabulku MAC adres (swconfig čipy jako Atheros/Qualcomm AR8xxx, MediaTek). Hloupé switche mezi zařízeními vidět nejsou: zařízení za nimi se ukážou u nejbližšího OpenWrt AP.
- Čáry ke klientům na Wi-Fi jsou tečkované, kabelové plné.

- **Druhy spojnic:** WAN (šedá, silná), LAN netagovaná (tenká), LAN + VLANy = trunk (silná; tagované VLANy jsou barevné štítky v uzlu AP), Wi-Fi (tečkovaná). Offline zařízení: červená přerušovaná.
- **Barvy = sítě:** netagovaná LAN a každá VLAN mají vlastní barvu, stejnou u routeru, AP i SSID. Legenda ukazuje VLAN ID a názvy rozhraní, pod kterými je síť na zařízeních.
- **VLAN ID** se bere z názvů zařízení (`eth0.30`, `br-lan.10`), ze členů bridge (`br-host` → `eth0.30`) a z `uci show network` (`ifname`/`device`/`ports`, i starší OpenWrt). Rozhraní s vlastní IP zařízení (nebo pojmenované `lan`) je vždy netagovaná LAN, i když je interně `eth0.1`. Když se VLAN na AP zjistit nepodaří, použije se rozhraní routeru se stejným názvem (bez ohledu na velikost písmen). Funguje se swconfig i DSA.
- Na AP se za WAN považuje jen rozhraní pojmenované `wan*`; VLAN rozhraní s vlastní bránou (DHCP nebo statická) se zobrazí jako VLAN. Na routeru je WAN rozhraní s výchozí trasou.
- VLANy, které jsou jen ve switchi (`switch_vlan` / `bridge-vlan`) bez rozhraní, se nezobrazí.
- Mapa ukazuje **logické** propojení: switche a fyzické porty z OpenWrt vidět nejsou.

### Dlaždice na přehledu

První dlaždice ukazuje **zařízení online**: router a všechny nastavené AP. Po najetí myší se ukáže seznam.

### Obnovování stránky

Otevřené stránky se obnovují po `refresh_interval` sekundách (0 = vypnuto). Každý prohlížeč si může nastavit vlastní hodnotu na stránce **Nastavení** add-onu (uloží se do cookie). Stránka SSID se sama neobnovuje, aby se neztratil rozepsaný formulář.

### Co se čte z AP

| Údaj | Příkaz |
|---|---|
| model, hostname, verze | `ubus call system board` |
| uptime, load, RAM | `ubus call system info` |
| overlay | `df -k /overlay` |
| rádia, SSID → síť/VLAN | `ubus call network.wireless status` |
| SSID včetně vypnutých | `uci -q show wireless` (bez hesel) |
| klienti (signál, rychlost, data) | `ubus call hostapd.<if> get_clients` |
| tabulka MAC adres switche (kabel na portech AP) | `swconfig dev <switch> get arl_table`, `/proc/net/arp` |
| rozhraní a VLANy (router, AP) | `ubus call network.interface dump`, `ubus call network.device status` |
| jména a IP klientů (router) | `ubus call luci-rpc getDHCPLeases`, jinak `/tmp/dhcp.leases` |

Funguje i na AP bez LuCI (stačí `wpad`/`hostapd` a `ubus`, které jsou v každém OpenWrt).

### JSON API

`…/api/state` vrací celý stav (AP + klienti) jako JSON.

### Jazyky

- **Webové rozhraní:** angličtina a čeština. Jazyk se vybírá v tomto pořadí: volba na stránce **Nastavení** add-onu (pro daný prohlížeč) → `language` v konfiguraci (pokud není `auto`) → jazyk uživatele Home Assistantu → jazyk prohlížeče → angličtina.
- **Konfigurace** v Home Assistantu (názvy a popisy voleb): `translations/en.yaml`, `cs.yaml`, HA vybere jazyk podle profilu uživatele.
- **Dokumentace:** tento soubor je dvojjazyčný (HA zobrazuje jeden `DOCS.md` pro všechny jazyky).

**Přidání jazyka** (např. němčiny):
1. Zkopírovat `app/i18n/en.json` na `app/i18n/de.json`, přeložit hodnoty (klíče a `{proměnné}` nechat), `_language_name` nastavit na `Deutsch`. Hodnoty, které jsou seznamem, jsou tvary množného čísla.
2. Pokud se pravidlo množného čísla liší od angličtiny, doplnit ho do `PLURALS` v `app/i18n.py`.
3. Do `config.yaml` doplnit `de` do `language: list(auto|cs|en|de)?`, volitelně `translations/de.yaml`.
Chybějící klíče se vezmou z angličtiny.

### Řešení problémů

| Příznak | Příčina |
|---|---|
| `PermissionDenied` | Veřejný klíč chybí v `/etc/dropbear/authorized_keys` na daném AP. |
| `TimeoutError` / `ConnectionRefused` | AP vypnutý, špatná IP, nebo dropbear neposlouchá na LAN. |
| Klienti bez jména/IP | `dhcp_router` nenastaven, klíč není na routeru, nebo statická IP. |
| Žádná SSID | Wi-Fi na AP vypnutá (`ubus list 'hostapd.*'` je prázdné). |
| „Přístup odepřen“ | Uživatel HA není v `full_access` ani `read_access`. |
| Všichni mají jen čtení | Ingress neposílá jméno uživatele (Nastavení ukáže „Přihlášen jako –“), nebo jméno neodpovídá uživatelskému jménu HA. |
| Add-on nenastartuje, `python-multipart` | Stará verze 0.2.0, aktualizovat. |
| Repozitář nejde přidat | Špatné místo (Marketplace), HA nevidí Git server (zkusit IP), špatný token. Detail: Nastavení → Systém → Protokoly → **Supervisor**. |

Logy: karta **Protokol** u add-onu.
