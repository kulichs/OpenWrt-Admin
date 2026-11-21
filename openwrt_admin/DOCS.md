# OpenWrt-Admin

Správa OpenWrt access pointů přímo v Home Assistantu: stav AP (uptime, load, RAM, volné místo na overlay), rádia, SSID a seznam připojených Wi-Fi klientů včetně jmen a IP z DHCP na routeru. Na AP se nic neinstaluje, vše jde přes SSH.

## Co umí měnit (od 0.2)

| Akce | Kde | Příkaz na AP |
|---|---|---|
| Restart | stránka AP | `reboot` |
| Zkontrolovat aktualizace | stránka AP | `apk update && apk list --upgradable` (u starších AP `opkg`) |
| Upgrade balíčků | stránka AP | `apk upgrade` (u starších AP `opkg upgrade …`) |
| Název, heslo, skrytí, zapnutí SSID | stránka SSID | `uci set wireless.<sekce>.… && uci commit wireless && wifi reload` |

Změna SSID se nejdřív zobrazí jako náhled a zapíše se až po potvrzení. Restart a upgrade se potvrzují dialogem.

> **Upgrade balíčků** OpenWrt obecně nedoporučuje, nové verze se instalují do overlay a na malých AP (UnifiAP1 s 8 MB flash) může místo dojít. Pro velké změny je lepší nový image a `sysupgrade`.

## Instalace (lokální add-on)

1. Do HA dostaň složku `openwrt_admin` do `/addons/`:
   - přes add-on **Samba share** (síťová složka `addons`), nebo
   - přes **Advanced SSH & Web Terminal**: `scp -r openwrt_admin root@homeassistant:/addons/`
2. **Nastavení → Doplňky → Obchod s doplňky → ⋮ → Zkontrolovat aktualizace.** Dole se objeví sekce **Místní doplňky** s „OpenWrt-Admin“.
3. **Nainstalovat.** Image se sestaví přímo na RPi4, trvá to pár minut.
4. Na kartě **Konfigurace** nastav AP (viz níže), pak **Spustit** a zapni **Zobrazit v postranním panelu**.

## Konfigurace

```yaml
ssh_user: root
poll_interval: 30            # s, 10–600
dhcp_router: 192.168.223.1   # router s DHCP (jména a IP klientů); prázdné = vypnuto; lze i "host:port"
demo: false                  # true = vymyšlená data, bez SSH
full_access:                 # uživatelé HA s plným přístupem (restart, apk, SSID)
  - radim
read_access:                 # uživatelé HA jen pro čtení
  - jana
aps:
  - name: OpenWrt_UnifiAP1
    host: 192.168.223.18
  - name: OpenWrt_Puda
    host: 192.168.223.x
  - name: OpenWrt_Sklep
    host: 192.168.223.y
    port: 22                 # volitelné
```

## Uživatelé a oprávnění (od 0.3)

Přihlášení řeší Home Assistant (Ingress), add-on vlastní hesla nemá. Podle uživatele HA, který panel otevřel, přidělí roli:

| Role | Kdo | Co smí |
|---|---|---|
| plný přístup | je v `full_access` | vše: restart, aktualizace balíčků, změny SSID |
| jen čtení | je v `read_access` | přehled, klienti, SSID, detail AP – bez tlačítek akcí |
| bez přístupu | není v žádném seznamu | stránka „Přístup odepřen“ |

- Do seznamů se píše **uživatelské jméno** HA (Nastavení → Lidé → Uživatelé), případně zobrazované jméno. Velikost písmen nehraje roli.
- **Oba seznamy prázdné:** plný přístup má každý, kdo panel otevře (stejně jako do 0.2). Stránka Nastavení na to upozorní.
- **Ingress nepošle jméno uživatele:** režim jen pro čtení, nikdy plný přístup.
- Kontroluje se na serveru, ne jen v zobrazení: akce uživatele s právem jen pro čtení skončí chybou.
- Panel je viditelný všem uživatelům HA (`panel_admin: false`). Samotné schování panelu by nestačilo, HA ingress adresu u běžného uživatele neblokuje.
- Kdo je přihlášený a proč má danou roli, ukazuje stránka **Nastavení → Uživatelé a oprávnění**. Akce se logují i se jménem (`radim: Restart OpenWrt_Puda`).

## SSH klíč

Při prvním startu si add-on vygeneruje klíč `ed25519` (uložený v `/data`, je součástí záloh HA). Veřejnou část najdeš v add-onu na stránce **Nastavení**. Přidej ji na každý AP i na router:

```sh
echo 'ssh-ed25519 AAAA… ha-openwrt-admin' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

`/etc/dropbear/authorized_keys` přežije běžný `sysupgrade`. Po `sysupgrade -n` ho přidej znovu, nebo ho rovnou zapiš ve first-boot skriptu (uci-defaults).

> Klíč se přihlašuje jako `root` a add-on umí AP restartovat a měnit jeho konfiguraci. Kdo má přístup do `/data` add-onu (zálohy HA), má root na AP. Zálohy HA proto šifruj.

## Co se čte z AP

| Údaj | Příkaz |
|---|---|
| model, hostname, verze | `ubus call system board` |
| uptime, load, RAM | `ubus call system info` |
| overlay | `df -k /overlay` |
| rádia, SSID → síť/VLAN | `ubus call network.wireless status` |
| SSID včetně vypnutých | `uci show wireless` (bez hesel) |
| klienti (signál, rychlost, data) | `ubus call hostapd.<if> get_clients` |
| jména a IP klientů (router) | `ubus call luci-rpc getDHCPLeases`, jinak `/tmp/dhcp.leases` |

Funguje i na AP bez LuCI (stačí `wpad`/`hostapd` a `ubus`, které jsou v každém OpenWrt).

## JSON API

`…/api/state` vrací celý stav (AP + klienti) jako JSON, např. pro pozdější senzory v HA.

## Řešení problémů

| Příznak | Příčina |
|---|---|
| `PermissionDenied` | Veřejný klíč není v `/etc/dropbear/authorized_keys` na daném AP. |
| `TimeoutError` / `ConnectionRefused` | AP je vypnutý, špatná IP, nebo SSH (dropbear) neposlouchá na LAN. |
| Klienti bez jména/IP | `dhcp_router` nenastaven, klíč není na routeru, nebo klient má statickou IP. |
| Žádná SSID | AP nemá zapnutou Wi-Fi (`ubus list 'hostapd.*'` je prázdné). |
| „Přístup odepřen“ | Uživatel HA není v `full_access` ani `read_access`. |
| Všichni mají jen čtení | Ingress neposílá jméno uživatele (Nastavení v add-onu ukáže „Přihlášen jako –“), nebo jméno v seznamu neodpovídá uživatelskému jménu HA. |

Logy: karta **Protokol** u add-onu.
