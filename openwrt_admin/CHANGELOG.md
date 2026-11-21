# Changelog

## 0.1.0
- První verze, pouze čtení: přehled AP, rádia, SSID, klienti, detail AP, JSON API.
- Jména a IP klientů z DHCP na routeru.
- SSH klíč generovaný add-onem, Ingress (panel v HA), demo režim.

## 0.1.1
- Tmavý režim podle tématu Home Assistantu (profil → Motiv), jinak podle systému.

## 0.1.2
- Přejmenováno na OpenWrt-Admin (slug `openwrt_admin`).

## 0.1.3
- U klientů pásmo (2,4/5/6 GHz) a vyjednaný standard: Wi-Fi 4 (n), 5 (ac), 6 (ax), 7 (be), případně 802.11a/b/g.
- Filtr klientů podle pásma, pásmo u SSID v detailu AP.

## 0.2.0
- Karta pro každý AP v horní liště (stav online/offline a počet klientů).
- Akce na stránce AP: **Restart**, **Zkontrolovat aktualizace** (`apk update` + seznam), **Upgrade balíčků** (`apk upgrade`). Na starších AP s `opkg` automaticky přes `opkg`. Varování při málo místa na overlay.
- Nová stránka **SSID**: změna názvu, hesla, skrytí a zapnutí/vypnutí najednou na více AP. Před zápisem náhled `uci` příkazů, pak `uci commit wireless && wifi reload`. Heslo se nikde nezobrazuje.
- SSID se čtou z `uci show wireless`, takže jsou vidět i vypnutá.
- Akce jdou spustit jen z rozhraní add-onu (ochrana proti CSRF), na jednom AP vždy jen jedna akce naráz.

## 0.2.1
- Oprava: add-on 0.2.0 nenastartoval, chyběla závislost `python-multipart` (formuláře SSID).
