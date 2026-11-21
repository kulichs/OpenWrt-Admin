# Home Assistant add-ony

| Add-on | Popis |
|---|---|
| [OpenWrt-Admin](openwrt_admin/) | Přehled OpenWrt AP a Wi-Fi klientů (SSH/ubus, jen čtení) |

## Instalace do HA

**Nastavení → Doplňky → Obchod s doplňky → ⋮ → Repozitáře** a přidat URL tohoto repozitáře.

Nová verze add-onu: zvednout `version` v `openwrt_admin/config.yaml` a doplnit `CHANGELOG.md`, pak `git push`. HA nabídne aktualizaci.
