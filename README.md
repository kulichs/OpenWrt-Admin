# Home Assistant add-ons

**[English](#english) · [Česky](#česky)**

## English

| Add-on | Description |
|---|---|
| [OpenWrt-Admin](openwrt_admin/) | Manage OpenWrt access points and Wi-Fi clients over SSH (`ubus`/`uci`): overview, clients, SSIDs, radios and channels (utilisation, neighbour scan, warnings, channel plan, editing channel/width/power), adding SSIDs, router, network map with VLANs, wired devices on AP ports and managed switches (SNMP), network statistics (interface speeds, ping, top internet users, switch port traffic, optional HA sensors), new-device notifications, update overview, configuration backups, disconnecting clients, reboot, permissions per Home Assistant user. UI in English and Czech. |

**Install:** Home Assistant → **Settings → Apps (Add-ons) → store → ⋮ → Repositories** → add the URL of this repository (or `ha store add <URL>` in the HA terminal). Not via *Marketplace → Add from link*, that one is GitHub/HACS only.

Full documentation: [openwrt_admin/DOCS.md](openwrt_admin/DOCS.md) (also shown in HA on the add-on's **Documentation** tab).

About the project, with screenshots (blog, in Czech): [OpenWrt-Admin – správa domácí OpenWrt sítě z Home Assistantu](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).

Developers: [CONTRIBUTING.md](CONTRIBUTING.md) (Czech) and project rules in [CLAUDE.md](CLAUDE.md).

**License:** [MIT](LICENSE) – free to use, modify and share, keep the copyright notice. Bundled htmx is 0BSD.

**New release:** bump `version` in `openwrt_admin/config.yaml`, add a `CHANGELOG.md` entry, `git push`. Home Assistant offers the update by itself.

## Česky

| Add-on | Popis |
|---|---|
| [OpenWrt-Admin](openwrt_admin/) | Správa OpenWrt AP a Wi-Fi klientů přes SSH (`ubus`/`uci`): přehled, klienti, SSID, rádia a kanály (vytížení, sken okolí, upozornění, návrh kanálů, úprava kanálu/šířky/výkonu), přidávání SSID, router, mapa sítě s VLANy, kabelovými zařízeními na portech AP a spravovanými switchi (SNMP), statistiky sítě (rychlosti rozhraní, ping, kdo vytěžuje internet, provoz na portech switche, volitelně senzory do HA), upozornění na nová zařízení, přehled aktualizací, zálohy konfigurace, odpojení klientů, restart, oprávnění podle uživatelů Home Assistantu. Rozhraní česky a anglicky. |

**Instalace:** Home Assistant → **Nastavení → Aplikace (Doplňky) → obchod → ⋮ → Repozitáře** → přidat URL tohoto repozitáře (nebo `ha store add <URL>` v terminálu HA). Ne přes *Marketplace → Přidat z odkazu*, ten bere jen GitHub/HACS.

Celá dokumentace: [openwrt_admin/DOCS.md](openwrt_admin/DOCS.md) (v HA i na kartě **Dokumentace** u add-onu).

O projektu se screenshoty na blogu: [OpenWrt-Admin – správa domácí OpenWrt sítě z Home Assistantu](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).

Pro vývojáře: [CONTRIBUTING.md](CONTRIBUTING.md) a pravidla projektu v [CLAUDE.md](CLAUDE.md).

**Licence:** [MIT](LICENSE) – volně k použití, úpravám i šíření, stačí zachovat uvedení autora. Přibalené htmx má licenci 0BSD.

**Nová verze:** zvednout `version` v `openwrt_admin/config.yaml`, doplnit `CHANGELOG.md`, `git push`. HA nabídne aktualizaci sám.
