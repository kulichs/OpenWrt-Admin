# OpenWrt-Admin

**[English](#english) · [Česky](#česky)**

---

## English

Manage OpenWrt access points from Home Assistant: AP status (uptime, load, RAM, free overlay space), radios, SSIDs and all connected Wi-Fi clients with names and IPs from the router's DHCP. Reboot APs, update packages and change SSIDs on several APs at once. Nothing is installed on the APs, everything goes over SSH (`ubus`, `uci`).

> The user interface is currently in Czech only (see [Languages](#languages)).

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
dhcp_router: 192.168.1.1     # router with DHCP leases (client names/IPs); empty = off; "host:port" works too
demo: false                  # true = made-up data, no SSH
full_access:                 # HA usernames with full access (reboot, packages, SSIDs)
  - alice
read_access:                 # HA usernames with read-only access
  - bob
aps:
  - name: AP-Living
    host: 192.168.1.2
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # optional
```

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
| Reboot | AP page | `reboot` |
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
| client names and IPs (router) | `ubus call luci-rpc getDHCPLeases`, else `/tmp/dhcp.leases` |

Works on APs without LuCI (only `wpad`/`hostapd` and `ubus`, present in every OpenWrt).

### JSON API

`…/api/state` returns the whole state (APs + clients) as JSON.

### Languages

- The **configuration** in Home Assistant (option names and descriptions) is translated to English and Czech (`translations/en.yaml`, `cs.yaml`); HA picks the language of the user's profile.
- This **documentation** is bilingual (HA shows one `DOCS.md` for all languages).
- The **web UI** of the add-on is Czech only for now.

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
dhcp_router: 192.168.1.1     # router s DHCP (jména/IP klientů); prázdné = vypnuto; lze i "host:port"
demo: false                  # true = vymyšlená data, bez SSH
full_access:                 # uživatelská jména HA s plným přístupem (restart, balíčky, SSID)
  - alice
read_access:                 # uživatelská jména HA jen pro čtení
  - bob
aps:
  - name: AP-Living
    host: 192.168.1.2
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # volitelné
```

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
| Restart | stránka AP | `reboot` |
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
| jména a IP klientů (router) | `ubus call luci-rpc getDHCPLeases`, jinak `/tmp/dhcp.leases` |

Funguje i na AP bez LuCI (stačí `wpad`/`hostapd` a `ubus`, které jsou v každém OpenWrt).

### JSON API

`…/api/state` vrací celý stav (AP + klienti) jako JSON.

### Jazyky

- **Konfigurace** v Home Assistantu (názvy a popisy voleb) je přeložená do angličtiny a češtiny (`translations/en.yaml`, `cs.yaml`), HA vybere jazyk podle profilu uživatele.
- Tato **dokumentace** je dvojjazyčná (HA zobrazuje jeden `DOCS.md` pro všechny jazyky).
- **Webové rozhraní** add-onu je zatím jen česky.

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
