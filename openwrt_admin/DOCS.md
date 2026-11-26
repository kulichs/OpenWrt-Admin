# OpenWrt-Admin

**[English](#english) · [Česky](#česky)**

---

## English

A Home Assistant app (apps were called *add-ons* before HA 2026.2) to manage OpenWrt access points: AP status (uptime, load, RAM, free overlay space), radios, SSIDs and all connected Wi-Fi clients with names and IPs from the router's DHCP. Reboot APs, update packages and change SSIDs on several APs at once. Nothing is installed on the APs, everything goes over SSH (`ubus`, `uci`).

The web UI is available in English and Czech (see [Languages](#languages)).

Blog post about the app with screenshots (in Czech): [OpenWrt-Admin – správa domácí OpenWrt sítě z Home Assistantu](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).

### Installation

**From a Git repository (recommended, Home Assistant offers updates by itself)**

1. **Settings → Apps (Add-ons) → store → ⋮ → Repositories**, add the repository URL, e.g. `https://git.example.com/you/OpenWrt-Admin`.
   - Or in the HA terminal: `ha store add <URL>` (gives a more precise error message).
   - Private repository: `https://user:TOKEN@git.example.com/you/OpenWrt-Admin` (read-only token).
   - Do **not** use *Settings → Marketplace → Add from link*: since HA 2026.11 it only accepts public GitHub/HACS repositories and cannot install apps ("Unable to load GitHub repository").
2. **⋮ → Check for updates**, install **OpenWrt-Admin** (the image is built on your device, a few minutes).
3. Set up the **Configuration** tab (below), **Start**, enable **Show in sidebar**.

**As a local app:** copy the `openwrt_admin` folder to `/addons/` (Samba share or SSH), then **⋮ → Check for updates**, it appears under **Local apps**. Updates: copy again and **Rebuild**.

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
ha_names: true               # device names from Home Assistant (by MAC), e.g. ESPHome
names:                       # own names by MAC, highest priority
  - mac: aa:bb:cc:dd:ee:ff
    name: Boiler-relay
notify_new_devices: true     # notification in HA when a never-seen device joins the network
notify_service: ""           # optional, e.g. notify.mobile_app_phone (also to your phone)
notify_random_mac: true      # false = no notifications for random (private) MACs of phones
new_device_hours: 24         # h, 0 = off: how long a device has the "new" tag
radio_scan: false            # true = Scan button on the Radios page (neighbouring networks)
update_check_hours: 12       # h, 0 = off: check packages to update and new OpenWrt releases
backup_interval_days: 7      # days, 0 = off: automatic configuration backup of every device
backup_keep: 10              # backups kept per device
aps:                         # all managed devices – the app uses only these
  - name: Router
    host: 192.168.1.1
    type: router             # one router: interfaces, DHCP leases, client names/IPs
  - name: AP-Living
    host: 192.168.1.2
    type: ap                 # default
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # optional
switches:                    # optional: managed switches read over SNMP (read-only)
  - name: Switch-Core
    host: 192.168.1.5
    community: public        # SNMP v2c read community (default public)
stats: true                  # Statistics page (speeds, ping, top internet users, switch port traffic)
stats_interval: 5            # s, 2–60: how often the router and switch counters are read
ping_targets:                # internet addresses pinged by the router (IP or hostname)
  - name: Cloudflare
    host: 1.1.1.1
  - name: NIC.CZ
    host: nic.cz
ping_devices: true           # also ping the ISP gateway, APs and switches
traffic_source: auto         # auto | nlbwmon | conntrack | off – who uses the internet most
top_clients: 10              # 3–50
ha_sensors: false            # true = statistics as sensor.openwrt_admin_* entities in HA
ha_sensors_interval: 30      # s, 10–600
```

`dhcp_router` and `router_name` from versions up to 0.7.0 still work but are deprecated: if `dhcp_router` is set and no device has `type: router`, the device with the same IP becomes the router (so it is never shown twice); otherwise a router is created from `dhcp_router`. Recommended: remove `dhcp_router` and set `type: router` on the router.

### SSH key

On first start the app generates an `ed25519` key (stored in `/data`, included in HA backups). The public key is shown in the app on the **Settings** page. Add it to every AP and to the router:

```sh
echo 'ssh-ed25519 AAAA… ha-openwrt-admin' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

`/etc/dropbear/authorized_keys` survives a normal `sysupgrade`; after `sysupgrade -n` add it again or put it in a first-boot script (uci-defaults).

> The key gives root rights on the devices and the app can reboot APs and change their configuration. Anyone with access to the app's `/data` (HA backups) has root on your APs. **Encrypt your HA backups.**

#### Recommended: a dedicated user instead of `root`

So that the device logs show what the app did and what you did yourself, let the app log in as its own user, e.g. `owadmin`, with root rights (UID 0). Nothing is installed; on every AP and the router:

```sh
grep -q '^owadmin:' /etc/passwd || echo 'owadmin:x:0:0:OpenWrt-Admin:/root:/bin/ash' >> /etc/passwd
grep -q '^owadmin:' /etc/shadow || echo 'owadmin:*:0:0:99999:7:::' >> /etc/shadow
```

Then set `ssh_user: owadmin` in the app configuration (or `user: owadmin` for a single device in `aps`). The Settings page shows the commands with your user name.

- The user has no password (`*`), it can only log in with the key.
- OpenWrt's dropbear reads the keys of **every UID 0 user** from `/etc/dropbear/authorized_keys`, so the app key stays there (a `~/.ssh` would be ignored). The same key would therefore still work for `root` too – the benefit is the clear trace in the log, not a technical restriction. A user without root rights would need `sudo` installed on every device and would run every command through it, which this app deliberately avoids.
- `/etc/passwd` and `/etc/shadow` survive a normal `sysupgrade`; after `sysupgrade -n` add the lines again (first-boot script).

#### Who did what (device syslog)

Every action started from the app (reboot, SSID changes, new SSID, radio settings, `apk`, backup, scan, client disconnect/block) is also written to the device syslog, with the tag `openwrt-admin` and the name of the Home Assistant user who started it; passwords are replaced with `********`. Regular data reading (every few seconds) is not logged. On the device:

```sh
logread -e openwrt-admin     # what was done and by which HA user
logread -e dropbear          # logins (with a dedicated user you see "owadmin" instead of "root")
```

### What it can do

| Action | Where | Command on the AP |
|---|---|---|
| Reboot | AP or router page | `reboot` |
| Check for updates | AP page | `apk update && apk list --upgradable` (older APs: `opkg`) |
| Upgrade packages | AP page | `apk upgrade` (older APs: `opkg upgrade …`) |
| SSID name, password, hidden, on/off | SSID page | `uci set wireless.<section>.… && uci commit wireless && wifi reload` |
| New SSID on selected radios | SSID page → Add a new SSID | `uci set wireless.owadmin_<ssid>_<radio>=wifi-iface` + device, mode, ssid, network, encryption, key → commit, `wifi reload` |
| Channel, width, power, country of radios | Radios page → Edit radios | `uci set wireless.<radio>.channel/htmode/txpower/country` → commit, `wifi reload` |
| Back up configuration | AP or router page | `sysupgrade -b /tmp/…` (archive transferred over SSH, then deleted on the device) |
| Disconnect / block a Wi-Fi client | client tables (AP page, Clients) | `ubus call hostapd.<if> del_client '{"addr":…,"deauth":true,"ban_time":…}'` |
| Start / stop / restart / enable / disable a service | device page → Services | `/etc/init.d/<service> …` (`dropbear`, `network`, `rpcd`, `log`: restart only, in the background) |
| Pause / resume adblock, reload lists | router page → Services → adblock | `/etc/init.d/adblock suspend` / `resume` / `reload`; timed pause: timer on the device (`/tmp/owadmin-adb-resume`) |
| Unblock / block a domain (adblock) | router page → Services → adblock | line in `/etc/adblock/adblock.allowlist` / `adblock.blocklist`, then `adblock reload` |
| Unblock / block an IP or network (banIP) | router page → Services → banIP | line in `/etc/banip/banip.allowlist` / `banip.blocklist`, then `banip reload` (only when enabled) |
| Static DHCP lease: add / edit / delete | router page → DHCP | `uci add dhcp host` / `uci set dhcp.<cfg…>.name/mac/ip/dns` / `uci delete dhcp.<cfg…>` → `uci commit dhcp`, `dnsmasq reload` (`uci revert` on error) |
| DHCP range, lease time, on/off | router page → DHCP | `uci set dhcp.<net>.start/limit/leasetime/ignore` → commit, `dnsmasq reload` |
| Local name / alias: add / edit / delete | router page → DNS | `uci add dhcp domain` / `uci add dhcp cname`, `uci set …`, `uci delete …` → `uci commit dhcp`, `dnsmasq reload` |
| Upstream DNS / forwarded domain in dnsmasq | router page → DNS | `uci add_list dhcp.<cfg…>.server=…`, `noresolv=1` → commit, `dnsmasq reload`, check `nslookup nic.cz 127.0.0.1`, rollback on failure |
| Upstream DNS on WAN | router page → DNS (red section) | `uci add_list network.wan.dns=…` (`peerdns=0`) → commit, `network reload` in the background, check, rollback on failure |
| Turn a firewall rule / port forward on or off | router page → Firewall | `uci set firewall.<cfg…>.enabled=0` / delete → commit, `fw4 check`, `firewall reload` – with the safety net (rollback after 90 s without confirmation) |

- **Adding an SSID:** name, password (8–63 characters), encryption WPA2 (`psk2`), WPA2/WPA3 (`sae-mixed`) or WPA3 (`sae`; both SAE variants also set `ieee80211w`), network (interface on the AP, e.g. `lan` or a VLAN network) and hidden; tick the radios (AP + band). The network must exist on each selected AP and the same SSID must not already be on that radio. WPA3 needs wpad with SAE (wpad-basic-mbedtls and newer).
- Editing SSIDs changes **existing** SSIDs (`wifi-iface` sections). Tick the APs/radios (bands) the change applies to. A preview of the `uci` commands is shown first; the change is written only after confirming. The password is never shown.
- Reboot and upgrade ask for confirmation.
- **Upgrading packages** is generally discouraged on OpenWrt: new versions go to the overlay and small APs (8 MB flash) may run out of space. For bigger changes build a new image and `sysupgrade`.

### Managed switches (SNMP)

Managed switches (Netgear GS7xxT, TP-Link, Cisco SG, Zyxel, HP…) can be added under `switches`. They are read over **SNMP v2c, read-only** – nothing is changed on the switch and nothing is installed. Standard MIBs are used, so any switch with SNMP works:

| Data | MIB / OID |
|---|---|
| model, firmware, uptime | SNMPv2-MIB `system` |
| ports: state, speed, duplex, name, description | IF-MIB `ifOperStatus`, `ifHighSpeed`, `ifName`, `ifAlias`; EtherLike-MIB |
| MAC addresses on ports (per VLAN) | Q-BRIDGE-MIB `dot1qTpFdbPort` (fallback BRIDGE-MIB `dot1dTpFdbPort`) |
| VLANs on ports (untagged / tagged) | Q-BRIDGE-MIB `dot1qVlanStaticEgressPorts`, `…UntaggedPorts` |

- **Map:** the switch is a node of its own. Its uplink is the port where the router's MAC is seen; APs plugged into it are drawn under it, each with its own line (with the port speed), wired devices on its ports are shown in a **LAN** group, coloured by their network (LAN, IoT, DMZ…). Devices behind an AP stay under that AP (the deepest device that sees a MAC locally wins).
- **Switch page:** system info, the **Ports** panel (all ports as boxes; the table lists connected ports with speed, VLANs `U:`/`T:` and devices) and the switch diagnostics (MAC table by port).
- **Setting up SNMP on the switch** (Netgear GS724Tv4: System → SNMP → SNMP V1/V2 → Community Configuration): Management Station IP = Home Assistant address (or the LAN, e.g. `192.168.1.0` / `255.255.255.0`), Community String = your community, Access Mode **Read Only**, Status **Enable**, then **Add** (not Apply).
- Test from any Linux machine: `snmpwalk -v2c -c <community> <switch> 1.3.6.1.2.1.17.7.1.2.2.1.2`.
- LLDP is not needed – the MAC table is enough.

### Ports and link speed

AP and router pages have a **Ports** panel: every physical port with its state, negotiated speed and duplex, its role and the devices plugged into it (from the switch MAC table).

- **swconfig** devices (switch chips such as Atheros/Qualcomm AR8xxx, MediaTek): ports of the switch from `swconfig dev <switch> show` (`link: port:N link:up speed:1000baseT full-duplex`). The CPU port is the AP itself, the uplink is the port towards the router. Only ports used in `switch_vlan` are shown (the chip often has more ports than the device has jacks); without `switch_vlan` the switch is unused and the device ports below are shown.
- **DSA** devices and devices without a switch (e.g. UniFi AP with one `eth0`, a router with two network cards): physical devices (`lan1`, `wan`, `eth0`…) from `ubus call network.device status` (`speed: "1000F"`). Only devices the network uses (bridge members, interface devices, `wan*`) are shown. Without a switch the only connected port counts as the uplink.
- Colours: green 1 Gb/s and more, orange 100 Mb/s, red 10 Mb/s, dashed = disconnected; half-duplex is flagged. **100 Mb/s instead of 1 Gb/s** usually means a damaged or 4-wire cable, or a device that cannot do more.
- On the **map** the line to each AP is labelled with the speed of its uplink port, and wired devices on AP ports show their port speed (green 1 Gb/s, orange below).

### Radios and channels

The **Radios** page lists every radio of every AP in one table and checks the settings:

| Column | Source |
|---|---|
| channel (and frequency), width, transmit power, noise, chip | `iwinfo` – the real values; with channel `auto` the channel the radio actually uses, power after hardware limits |
| configured channel and power | `uci`/`network.wireless status` (shown next to the real value when they differ) |
| channel utilisation now, average and chart of the last hour | `iw dev <if> survey dump` – `channel busy time` / `channel active time` between two AP readings (every `poll_interval`) |

- **Warnings and tips:** two of your APs on the same or overlapping channel, 2.4 GHz outside channels 1/6/11, 40 MHz on 2.4 GHz, 5 GHz only 20 MHz wide, channel `auto`, DFS channel (52–144), channel busy over 50 %, high noise, 2.4 GHz transmitting stronger than 5 GHz on the same AP (phones then stick to 2.4 GHz), different or missing country.
- **Channel map** for each band: which channels every AP occupies with its width; channels 1/6/11 and DFS channels are highlighted.
- The AP page shows the real channel, width, power and channel utilisation in its radio table.
- With `ha_sensors` each radio also gets `sensor.openwrt_admin_<ap>_<radio>_channel_utilization` (%).
- **Neighbour scan** (option `radio_scan`, off by default, full access only): **Scan** button for each radio or for all of them. It runs `iwinfo <if> scan`; the radio hops between channels for a few seconds, so connected clients may notice a short drop. Results stay in memory until the app restarts:
  - a list of neighbouring networks per radio (SSID, BSSID, channel, width incl. secondary channel, signal, encryption); your own APs are recognised by BSSID (from `iwinfo`) or SSID and marked,
  - channel occupancy bars under the channel map (number of foreign networks per channel, height by signal strength),
  - warnings: strong foreign networks (≥ −70 dBm) on your channel and a less busy channel suggestion (2.4 GHz: 1/6/11; 5 GHz: blocks within 36–48 for your width – DFS and 149–165, which the EU allows only at low power, are not suggested).
- **Edit radios** (full access): one form for all radios – channel (allowed channels from `iwinfo freqlist`, DFS marked, `auto`), width (from `iwinfo htmodelist`; older iwinfo returns nothing, then by the radio family: an 802.11n radio never gets VHT80), power (`iwinfo txpowerlist` or `auto`) and the country for all radios. Only values that differ from the current settings are changed. **Preview changes** shows the `uci` commands per AP, **Apply** writes them (`uci commit wireless && wifi reload`, on error `uci revert wireless`). Clients disconnect for a few seconds; a DFS channel stays silent for about a minute.
- **Propose settings** pre-fills the form: your APs on different channels (radios with the most clients choose first), away from channels busy with neighbours according to the last scan (2.4 GHz 1/6/11, 5 GHz 36–48; with *allow DFS channels* also 52–144), width 20 MHz on 2.4 GHz and as wide as the radio can (max. 80 MHz) on 5 GHz, power: 5 GHz the highest allowed, 2.4 GHz 3 dB lower. Every change has a reason. Nothing is written until you preview and apply. Without a scan the proposal only separates your own APs.

### Services, adblock and banIP

The **Services** tab on each device page (router and AP: Overview | Services) lists the services of that device (`ubus call service list`, start on boot from the `S..` links in `/etc/rc.d`): running, finished (one-shot scripts such as adblock with exit code 0), stopped, failed (exit code ≠ 0 while enabled on boot), no process (init scripts without a procd service, e.g. `firewall`), and whether it starts on boot. Scripts that only set something up at boot (`boot`, `sysctl`, `led`…) are hidden. The status is read when the tab opens (kept for 60 s, re-read after every action) – read only, nothing is written to the device log.

With full access: start, stop, restart, start on boot / don't start. Every action shows the command first and runs after **Apply**. `dropbear`, `network`, `rpcd`, `log`, `ubus`, `system` can only be restarted, and the restart runs in the background after 2 s – without them the app could not reach the device.

**adblock** (if `/etc/init.d/adblock` exists): state, blocked domains, lists, last run and DNS backend.
- **Not blocking** = adblock is enabled but the blocklist (`adb_list.overall`) is empty – typically paused from LuCI/CLI and forgotten, or the lists were not downloaded after the router booted. Use **Resume blocking** or **Reload lists**.
- **Pause** for 15 min, 1 h, 4 h or until resumed. A timed pause is resumed by a timer on the router (a background `sleep` checking `/tmp/owadmin-adb-resume`), so it works even if Home Assistant restarts meanwhile; a router reboot cancels the pause (adblock starts normally). The page shows when blocking resumes and warns if the time has passed and adblock is still paused.
- **Why is it blocked?** runs `/etc/init.d/adblock search <domain>` (`query` on older versions – detected automatically): the active list and the backups of the individual lists (which list contains the domain). Allowed for read-only users too.
- **Unblock (allow) / Block** adds the domain to `/etc/adblock/adblock.allowlist` / `adblock.blocklist` (and removes it from the other list), then `adblock reload` (tens of seconds). The allowlist matches exactly – `ads.example.com` does not allow `x.ads.example.com`. The lists below can be edited with ✕.

**banIP** (if `/etc/init.d/banip` exists): state (disabled / running / not running), set elements, lists. **Why is it blocked?** runs `/etc/init.d/banip search <IP>` and shows the set (e.g. `blocklistv4`, `cinsscorev4`). **Unblock / Block** works with `/etc/banip/banip.allowlist` / `banip.blocklist` (IPv4/IPv6 address or CIDR network); the sets are reloaded only when banIP is enabled. banIP itself (feeds, countries, interfaces) is configured in LuCI.

### DHCP (router)

The **DHCP** tab on the router page (Overview | Services | DHCP) works with dnsmasq on the router (`/etc/config/dhcp`):

- **Networks and ranges** – for every network with DHCP: subnet, router address, dynamic range (network address + `start` … + `start` + `limit` − 1, as in LuCI), lease time and usage: static leases inside the range, dynamic leases, free addresses. With full access: edit start, count, lease time, turn DHCP on/off. Networks with a static address and no DHCP section are listed as "No DHCP" with an option to enable it. A warning appears when 5 or fewer addresses are free.
- **Static leases** (`dhcp.@host`) – name, MAC, IP, network, "name in DNS" (`dns=1`), online state; search. Add / edit / delete. The suggested IP for a new lease is the first free address **below** the dynamic range (not used by another reservation, lease, ARP entry or local name), so the range stays free for other devices.
  - Errors stop the change: invalid name (letters, digits, hyphen), MAC or IP, IP outside the router networks, network/broadcast/router address, IP or MAC already in another reservation.
  - Warnings are shown in the preview: the IP is currently leased to another device, lies inside the dynamic range, is used by a local name, or the network has no DHCP.
- **Current leases** (`/tmp/dhcp.leases`) with the remaining time; **Make static** fills the add form with the name, MAC and current IP.

Every change: preview of the `uci` commands → **Apply** → `uci commit dhcp` and `/etc/init.d/dnsmasq reload` (DNS and DHCP keep running); on error `uci revert dhcp`. Before the first change of the day the app makes a configuration backup of the router (as the Backup button does). Anonymous sections are read with `uci -X` (names like `cfg0a1b2c`), so an edit targets the right lease even if another one was added in the meantime. A new static IP applies once the device renews its lease. IPv6 (odhcpd) is not changed.

The tab also warns about an unmerged `/etc/config/dhcp.apk-new` (or `dhcp-opkg`) – the default configuration left after a package upgrade; compare it with `diff -u` and delete it. Other such files (luci, uhttpd…) are listed in one collapsed line – they are harmless, apk never overwrites your modified configuration.

### DNS (router)

The **DNS** tab on the router page (Overview | Services | DHCP | DNS) works with the same dnsmasq configuration (`/etc/config/dhcp`):

- **Lookup test** – runs `nslookup <name> 127.0.0.1` on the router, i.e. asks dnsmasq exactly like clients do. It shows the address(es), the alias chain (alias → target → IP), **blocked** when the answer is `0.0.0.0` (adblock) and **NXDOMAIN** when the name does not exist; the raw output is in a collapsible line. Read only, available to read-only users.
- **Local names** (`dhcp.@domain`, "Hostnames" in LuCI) – name → IPv4/IPv6 address; add, edit, delete, search. A bare name (`grafana`) also resolves as `grafana.<local domain>`; a name in your own domain (`server.home`) or a public domain pointed to an internal address at home (split DNS, e.g. your web server in the DMZ) works too. Errors: invalid name or address, the same name with the same address twice, a name that is already an alias. Warnings: the name already has another address (it will have two), the address is outside the router networks.
- **Aliases** (`dhcp.@cname`) – alias → target name; add, edit, delete. dnsmasq answers an alias only when it knows the target itself (local name, static or current lease) – otherwise the preview warns and the list shows "?".
- **Upstream servers** – the panel shows which servers are used now (from WAN via `resolv.conf.auto`, or from dnsmasq when `noresolv` is set). Two ways to change them:
  - **Servers in dnsmasq (recommended)** – the dnsmasq `server` list with `noresolv=1`; it takes precedence over the WAN servers and changes with a dnsmasq reload only, without an internet outage. An empty field goes back to the WAN servers. Presets: CZ.NIC ODVR, Cloudflare, Quad9, Google. **Forwarded domains** (`server=/domain/ip`, e.g. a company domain behind a VPN) are added and removed here too.
  - **Servers on the WAN interface** (red section) – `network.wan.dns` (and `peerdns=0` for DHCP/PPPoE WAN); a network reload, so the internet may drop for a few seconds. The script runs in the background on the router, so a dropped SSH session cannot interrupt it halfway. While dnsmasq has its own servers, the WAN servers are not used (the preview warns).
  - Safety: before saving, the router asks each new server (`nslookup nic.cz <server>`) – non-answering servers are flagged, and if none answers the change is refused. After saving, the router resolves `nic.cz` through itself (several attempts); if that fails, it restores the previous settings (servers, `noresolv`, `peerdns`) on its own. Servers with their own port (`127.0.0.1#5053`, a local DoH/DoT proxy) are not tested in advance.
- **DNS settings** (read only) – local domain, how many static leases put their name into DNS (`dns=1`), rebind protection and its exceptions.

Changes work exactly like DHCP: preview → **Apply** → `uci commit dhcp` + `/etc/init.d/dnsmasq reload`, `uci revert dhcp` on error, a configuration backup before the first change of the day, a syslog entry with the HA user. Clients may cache an old answer for a while; the lookup test shows the router's answer immediately.

### Firewall (router)

The **Firewall** tab on the router page reads fw4 (on fw3 – OpenWrt 21.02 and older – it shows a limited read-only overview): `uci show firewall`, the real zone order per interface from `fw4 print` (which zone gets traffic from an interface first) the listening services from `netstat` (or `ss`) and the WAN interface from the default route (`ip route`), so it works whatever the WAN interface is called.

- **Checks** at the top: the WAN interface in a zone with input ACCEPT (the router is reachable from the internet except for what a rule blocks – the open services are listed), the WAN zone with forward ACCEPT, a network or an interface in several zones (only the first one applies, the rules of the others do not), WAN without masquerading, old fw3 includes (`/etc/firewall.user`) that fw4 ignores.
- **Open from the internet** – port forwards (`redirect` from WAN), ACCEPT rules from WAN and services on the router with their state: open (nothing blocks them), allowed by a rule, blocked by a rule, port forwarded elsewhere (DNAT goes first), closed by the zone policy. The full list of listening services is in a collapsible section.
- **Zone map** – rows = from, columns = to; the "router" column is input to the router itself (zone input policy), the other columns are forwardings between zones, the diagonal is the forward policy inside the zone. "+ N rules" = exceptions for specific ports or addresses.
- **Port forwards** and **rules** in configuration order (first match wins) – zones, protocol, ports, source IP/MAC, time conditions; rules that drop traffic of specific devices are tagged "block". Custom files in `/etc/nftables.d/` are only listed.

With full access, existing rules and port forwards can be **turned on and off** (turning on a port forward warns that it opens a port). More editing (blocking devices including time limits, port forwards, rules) comes next.

**Safety net when saving.** Firewall changes (and later network and VLAN changes) are saved like LuCI's "apply with rollback": the device backs up the affected configuration to `/tmp/owadmin-rb/<token>/`, starts a 90 s countdown, applies the change in the background (so a dropped SSH session cannot interrupt it) and runs `fw4 check` – if the check fails, the backup is restored immediately. The app then opens a new SSH connection and confirms. If it cannot reconnect (the change cut it off), the device restores the backup and reloads after 90 s on its own and logs `rollback … not confirmed`. Before the first change of the day the configuration is backed up as with DHCP.

### Statistics

The **Statistics** page (option `stats`, on by default) shows what the network is doing right now and in the last hour:

| Panel | Source |
|---|---|
| WAN download/upload, interfaces (WAN, LAN, every VLAN, tunnels): RX/TX, packets/s, peak, chart of the last hour | router `/proc/net/dev` |
| router CPU temperature and load | `/sys/class/thermal/thermal_zone*/temp`, `/proc/loadavg` |
| ping: internet targets (`ping_targets`), ISP gateway, APs and switches – last/average response, loss, history strip | `ping -c 1 -W 1` on the router; the router itself = SSH response time from the app |
| who uses the internet most (top `top_clients`) | **nlbwmon** (`nlbw -c json -g mac,ip`) when it runs on the router, otherwise **conntrack** (`/proc/net/nf_conntrack`) |
| current traffic of Wi-Fi clients | difference of hostapd `bytes` between two AP readings |
| busiest switch ports – also traffic between devices inside the network (e.g. laptop ↔ server) that does not pass the router | SNMP IF-MIB `ifHCInOctets`/`ifHCOutOctets` (fallback 32-bit `ifInOctets`/`ifOutOctets`) |

- The router and the switches are read every `stats_interval` seconds (default 5) over **one open SSH connection** (no new login every time, nothing in the router log). APs are still read every `poll_interval`.
- Nothing is installed on the devices. `nlbwmon` is used only if it is already installed; `traffic_source` can force `nlbwmon`, `conntrack` or switch it `off`.
  - **nlbwmon** refreshes its data every `refresh_interval` (default 30 s), so the speed is an average over ~90 s. It also gives the total for the current accounting period.
  - **conntrack** needs `net.netfilter.nf_conntrack_acct=1` (OpenWrt default; check with `cat /proc/sys/net/netfilter/nf_conntrack_acct`). Only IPv4 traffic between LAN/VLANs and the internet is counted, including port forwards. With **flow offloading** packets bypass conntrack and part of the traffic is missing.
- Ping targets must be an IP address or a hostname; nothing else is pinged (no scanning).
- The history (1 hour) is kept only in memory and is lost when the app restarts. For long-term history turn on `ha_sensors`.
- **Map:** lines to APs and switches show the current traffic on their link (`↓` towards the device / `↑` from it) from the switch port counters, the line from the internet shows WAN traffic, wired devices on switch ports show their port traffic. Over half of the port speed is red and thick.
- The **switch page** has a *Traffic* column in the port table, the **router page** shows RX/TX now for every interface, the **Clients** tables have a *Now ↓/↑* column and the overview has *Internet* and *WAN* tiles.

#### Sensors in Home Assistant

With `ha_sensors: true` the app writes the statistics every `ha_sensors_interval` seconds to Home Assistant through its REST API (`homeassistant_api`, no MQTT needed):

| Entity | State |
|---|---|
| `sensor.openwrt_admin_<router>_<interface>_rx` / `_tx` | Mbit/s (`device_class: data_rate`) |
| `sensor.openwrt_admin_<router>_<interface>_packets` | packets/s |
| `sensor.openwrt_admin_<router>_cpu_temperature`, `…_load` | °C, load |
| `sensor.openwrt_admin_ping_<name>` / `binary_sensor.openwrt_admin_ping_<name>` | response in ms (attribute `loss_1h`) / connectivity on/off |
| `binary_sensor.openwrt_admin_internet` | on when at least one internet target answers |
| `sensor.openwrt_admin_top_clients` | number of active clients; attribute `clients` = list (name, IP, MAC, download, upload in Mbit/s) |
| `sensor.openwrt_admin_<switch>_port<N>_down` / `_up` | Mbit/s, connected ports only |
| `sensor.openwrt_admin_<ap>_<radio>_channel_utilization` | % (attributes channel, htmode, txpower, noise) |

The entities are created on the first write. They have no `unique_id`, so they cannot be renamed in the UI, and after an HA restart they appear again with the next update. They have `state_class: measurement`, so HA keeps long-term statistics and they can go to InfluxDB and be shown in **Grafana**. The Settings page shows how many entities are sent and the last error.

### New devices on the network

When a device (MAC address) the app has never seen appears on the network – on Wi-Fi, on an AP port or in the router's ARP table – a notification is sent to Home Assistant (the bell, `persistent_notification`). It shows the name (if known), MAC, IP, network/VLAN and where the device is (AP + SSID, AP port, router). With `notify_service` (e.g. `notify.mobile_app_phone`) it also goes to that service.

- On the **first start** all devices currently on the network are taken as known – no flood of notifications. The list is in `/data/known_devices.json` (delete the file to start over).
- Devices seen for the first time in the last `new_device_hours` hours (default 24, 0 = off) have a **new** tag in the client tables and on the router page.
- Phones use a **random (private) MAC** per Wi-Fi network; such addresses are marked in the notification. `notify_random_mac: false` skips them.
- APs and the router themselves are never reported.

### Updates

Every `update_check_hours` (default 12) the app runs `apk update` + `apk list --upgradable` (older devices `opkg update` + `opkg list-upgradable`) on each device, one after another. The overview shows the number of packages to update (tile and on every card), the device page lists them with the installed and new version. The **Check for updates** button refreshes the list right away; after **Upgrade packages** it is checked again.

A new **OpenWrt release** is looked up in `https://downloads.openwrt.org/.versions.json` (needs internet from HA): a newer release of the same series (e.g. 25.12.2 → 25.12.3) has priority, otherwise a newer stable series. The firmware itself is not upgraded by the app – use firmware-selector and `sysupgrade`.

### Configuration backups

The **Back up now** button on an AP or router page (full access) and, every `backup_interval_days` (default 7), an automatic backup run `sysupgrade -b` on the device. The archive is transferred over SSH and stored in the app's `/data/backups/<device>/<device>-YYYY-mm-dd_HHMMSS.tar.gz`; the last `backup_keep` (default 10) are kept. `/data` is part of Home Assistant backups, so the AP configurations are backed up with HA.

- The archive contains **Wi-Fi passwords and keys** – only users with full access can download it (link in the backup list).
- **Restore:** upload the file to the device (e.g. `scp file.tar.gz root@ap:/tmp/`) and run `sysupgrade -r /tmp/file.tar.gz && reboot`, or use LuCI → System → Backup / Flash Firmware → Restore. The app does not restore by itself.
- Which files are included is set on the device in `/etc/sysupgrade.conf` (same as for `sysupgrade`).

### Disconnecting and blocking Wi-Fi clients

In the client tables (AP page, **Clients**) users with full access have a **Disconnect…** menu:
- **disconnect only** – the client is disconnected (deauthentication) and usually reconnects right away; useful to make it roam to a closer AP or to reconnect a stuck device,
- **+ block 10 min / 1 h / 24 h** – disconnected and refused for that time on **all APs broadcasting the same SSID** (otherwise it would just join another AP).

The block is kept in `hostapd` memory: a Wi-Fi restart (`wifi reload`, SSID change) or an AP reboot clears it. A permanent block is a MAC filter (`macfilter`/`maclist`) in the SSID configuration – not done by the app.

Blocked clients are listed above the client table (Clients page, AP page) with the APs and the remaining time; **Unblock** lifts the block on all APs with that SSID (`del_client` with `ban_time: 0`). The list comes from `ubus call hostapd.<if> list_bans`; on older hostapd without it only blocks set from the app are shown.

### Users and permissions

Login is handled by Home Assistant (Ingress); the app has no passwords of its own. It assigns a role based on the HA user who opened the panel:

| Role | Who | Can |
|---|---|---|
| full access | in `full_access` | everything: reboot, packages, SSID changes, backups (incl. download), disconnecting clients |
| read-only | in `read_access` | overview, clients, SSIDs, AP details, list of updates and backups – no action buttons, no backup download |
| no access | in neither list | "Access denied" page |

- Use the HA **username** (Settings → People → Users) or the display name, case-insensitive.
- **Both lists empty:** everyone who opens the panel has full access. The Settings page warns about it.
- **Ingress sends no username:** read-only, never full access.
- Permissions are enforced on the server, not only hidden in the UI. Actions are logged with the user name.
- The panel is visible to all HA users (`panel_admin: false`). Hiding the panel alone would not be enough: HA does not block the ingress URL for regular users.

### Device names

Devices with a static IP (typically ESPHome) never ask the router for DHCP, so the router does not know their name. The app therefore combines several sources; the name is taken in this order:

1. **`names`** in the app configuration (manual, tag *manual*),
2. **Home Assistant** – device registry by MAC (ESPHome, Shelly, …; tag *HA*). The app has `homeassistant_api: true` and reads `config/device_registry/list` over the Supervisor websocket every 10 minutes; switch off with `ha_names: false`,
3. **DHCP lease** on the router,
4. **static DHCP reservation** on the router (`uci show dhcp`, `host` sections; tag *reservation*).

IPs come from the DHCP lease, the reservation, or the ARP tables of the router and APs (marked *static IP*). The router page lists all devices the router knows (lease, reservation or ARP); those that are not on the network right now are in a collapsed **Inactive** section. A device counts as present when it is a Wi-Fi client, is seen on a switch port of an AP, or has a complete entry in the ARP table of the router or an AP – reservations and old DHCP leases alone do not count. The map shows only present devices. The Settings page shows how many names came from each source.

### Router

The router (the device with `type: router`) is a device of its own, without radios and SSIDs. It has its own tab, card on the overview and page with:
- system info (model, firmware, uptime, load, RAM, overlay),
- **interfaces** from `ubus call network.interface dump`: WAN/LAN/VLANs, state, protocol, IPv4/IPv6, gateway, uptime,
- **DHCP leases**: every device with an IP from the router, Wi-Fi clients with their AP/SSID/band, the rest marked as wired or not on Wi-Fi,
- the **network** of each device (LAN / VLAN and interface name, by the subnet its IP belongs to) and the number of active devices per interface,
- the same actions as APs: reboot, check for updates, upgrade packages. Rebooting the router takes the whole network and the internet down for a moment.

The app's SSH key must be on the router too.

ARP entries on the WAN and on tunnel/VPN interfaces (WireGuard `wg*`, PPPoE, OpenVPN…) are ignored – the ISP gateway or VPN peers are not clients.

### Network map

The **Map** page draws the network as a tree: Internet → router → access points → SSIDs → **clients**, plus a node for wired/other DHCP clients with those devices under it. Each node links to its page (SSID nodes to the filtered client list); hover over a client for IP, MAC, band and signal.

- **Clients** are shown with name (or MAC) and signal (green ≥ −60 dBm, orange ≥ −72 dBm, red below); wired devices with their IP. The **Clients** toggle switches them off (then SSIDs show only the count).
- **SSIDs without clients are hidden** by default; the **SSIDs without clients** toggle shows them.
- **Zoom and pan:** mouse wheel zooms around the cursor, dragging moves the map, buttons **+ / − / Fit**. The position is kept when the page refreshes itself.
- **APs working as switches:** wired devices plugged into an AP's ports are shown under that AP in a **LAN** group with the port number, and an AP plugged into another AP is drawn under it (elbow line). Source: the switch MAC table on each AP (`swconfig dev switch0 get arl_table`); the uplink is the port where the router's MAC is seen (from the AP's ARP table), the CPU port is the one tagged in all `switch_vlan`s. If the same MAC is seen on several chained APs, it belongs to the last one. Wired devices that are on no AP port stay under the router, **split by network**: **LAN / other** for the untagged LAN and one branch per VLAN (e.g. **VLAN 100 · dmz**) in that network's colour. The network is the router interface whose subnet contains the device IP. The router page shows for each DHCP lease whether it is on Wi-Fi, on an AP port, or elsewhere.
- **Last known place:** a switch forgets a MAC after a few minutes without traffic (e.g. a TV that is switched off). The app remembers where each wired device was last seen (AP + port, stored in `/data/wired_seen.json`) and keeps it there, greyed out with "last seen …", while it still has a DHCP lease on the router (without a lease for 24 hours). Entries older than 30 days are deleted.
- **Quiet devices:** a device that rarely sends anything (e.g. an IP phone) drops out of the switch table before it is ever seen. Once every 4 minutes the router pings wired devices whose place is unknown (one `ping -c1 -W1` each, at most 64), so the AP switch learns them again and the next refresh places them.
- **Switch diagnostics** (collapsible section at the bottom of an AP page): every switch port with its MAC addresses and role (uplink, CPU, local), whether the AP sees the router in its ARP table, and the "last seen" memory for that AP. Use it when a device appears in the wrong place.
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

Open pages reload every `refresh_interval` seconds (0 = off). Each browser can choose its own value on the app's **Settings** page (stored in a cookie). The SSID page never reloads by itself, so a half-filled form is not lost.

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
| waking quiet wired devices (router, every 4 min) | `ping -c1 -W1 <ip>` |
| packages to update (every `update_check_hours`) | `apk update -q; apk list --upgradable` / `opkg update; opkg list-upgradable` |
| port speeds | `swconfig dev <switch> show`, `ubus call network.device status` |
| configuration backup | `sysupgrade -b` |
| interfaces and VLANs (router, APs) | `ubus call network.interface dump`, `ubus call network.device status` |
| client names and IPs (router) | `ubus call luci-rpc getDHCPLeases`, else `/tmp/dhcp.leases`; `uci show dhcp` (reservations), `/proc/net/arp` |
| device names (Home Assistant) | websocket `config/device_registry/list` (MAC connections) |
| statistics (router, every `stats_interval`) | `/proc/net/dev`, `/sys/class/thermal`, `/proc/loadavg`, `ping -c 1 -W 1`, `nlbw -c json -g mac,ip` or `/proc/net/nf_conntrack` |
| switch port traffic | SNMP `ifHCInOctets`, `ifHCOutOctets` |
| radios: real channel, width, power, noise; channel utilisation | `iwinfo`, `iw dev <if> survey dump` |
| neighbouring networks (only on Scan, `radio_scan`) | `iwinfo <if> scan` |
| services, adblock, banIP (only when the Services tab opens) | `ubus call service list`, `ls /etc/init.d /etc/rc.d`, `/etc/init.d/adblock status`, `/etc/init.d/banip status`, allow/block list files |
| DHCP and DNS (only when the DHCP or DNS tab opens; lookup test: `nslookup <name> 127.0.0.1`) | `uci -X show dhcp`, `uci show network` (proto/ipaddr/netmask), `/tmp/dhcp.leases`, `resolv.conf.auto`, `ls /etc/config/*.apk-new` |
| firewall (only when the Firewall tab opens) | `uci -X show firewall`, `fw4 print` (zone order per interface), `netstat -tulpn` / `ss -tulpn`, `ip route` + `ip addr` (WAN), `ls /etc/nftables.d` |

Works on APs without LuCI (only `wpad`/`hostapd` and `ubus`, present in every OpenWrt).

### JSON API

`…/api/state` returns the whole state (APs + clients) as JSON, `…/api/stats` the current statistics (interfaces, ping, top clients, Wi-Fi traffic, switch ports).

### Languages

- **Web UI:** English and Czech. The language is chosen in this order: the choice on the app's **Settings** page (per browser) → `language` in the configuration (if not `auto`) → the language of the Home Assistant user → the browser's language → English.
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
| Switch: `SnmpError: SNMP timeout` | Wrong IP or community, SNMP not enabled, or the Home Assistant address is not allowed (Management Station IP). |
| No notification about a new device | `notify_new_devices` off, the device was already known (first start = baseline), or the call to HA failed (Settings page shows the error). |
| Backup fails | `sysupgrade -b` missing or `/tmp` full on the device; the error is shown after **Back up now** and in the app log. |
| Wired device under the router instead of its AP | Open the AP page → **Switch diagnostics**: is the uplink detected, is the MAC on a local port, is it in "last seen"? Quiet devices are found within a few minutes (router ping). |
| "Access denied" | HA user is in neither `full_access` nor `read_access`. |
| Everyone is read-only | Ingress sends no username (Settings shows "Přihlášen jako –"), or the name does not match the HA username. |
| App does not start, `python-multipart` | Old version 0.2.0, update. |
| Repository cannot be added | Wrong place (Marketplace), HA cannot reach the Git server (try the IP), wrong token. Details: Settings → System → Logs → **Supervisor**. |

Logs: the app's **Log** tab.

---

## Česky

Aplikace pro Home Assistant (do verze HA 2026.2 se aplikacím říkalo *add-ony*) na správu OpenWrt access pointů: stav AP (uptime, load, RAM, volné místo na overlay), rádia, SSID a všichni připojení Wi-Fi klienti se jmény a IP z DHCP na routeru. Restart AP, aktualizace balíčků a změna SSID na více AP najednou. Na AP se nic neinstaluje, vše jde přes SSH (`ubus`, `uci`).

Článek o aplikaci se screenshoty na blogu: [OpenWrt-Admin – správa domácí OpenWrt sítě z Home Assistantu](https://tadyk.info/openwrt-admin-sprava-domaci-openwrt-site-z-home-assistantu.html).

### Instalace

**Z Git repozitáře (doporučeno, HA nabízí aktualizace sám)**

1. **Nastavení → Aplikace (Doplňky) → obchod → ⋮ → Repozitáře**, přidat URL repozitáře, např. `https://git.example.com/ty/OpenWrt-Admin`.
   - Nebo v terminálu HA: `ha store add <URL>` (dá přesnější chybovou hlášku).
   - Soukromé repo: `https://uzivatel:TOKEN@git.example.com/ty/OpenWrt-Admin` (token jen pro čtení).
   - **Ne** přes *Nastavení → Marketplace → Přidat z odkazu*: od HA 2026.11 bere jen veřejné repozitáře z GitHubu/HACS a aplikace neinstaluje („Nelze načíst repozitář GitHub“).
2. **⋮ → Zkontrolovat aktualizace**, nainstalovat **OpenWrt-Admin** (image se staví na zařízení, pár minut).
3. Nastavit kartu **Konfigurace** (níže), **Spustit**, zapnout **Zobrazit v postranním panelu**.

**Jako lokální aplikace:** složku `openwrt_admin` zkopírovat do `/addons/` (Samba share nebo SSH), pak **⋮ → Zkontrolovat aktualizace**, objeví se v **Místní doplňky**. Aktualizace: znovu zkopírovat a **Přestavět**.

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
ha_names: true               # jména zařízení z Home Assistantu (podle MAC), např. ESPHome
names:                       # vlastní jména podle MAC, nejvyšší priorita
  - mac: aa:bb:cc:dd:ee:ff
    name: Rele-kotel
notify_new_devices: true     # upozornění v HA, když se v síti objeví zařízení, které aplikace ještě neviděla
notify_service: ""           # volitelně, např. notify.mobile_app_telefon (i do mobilu)
notify_random_mac: true      # false = neupozorňovat na náhodné (soukromé) MAC telefonů
new_device_hours: 24         # h, 0 = vypnuto: jak dlouho má zařízení štítek „nové“
radio_scan: false            # true = tlačítko Skenovat na stránce Rádia (okolní sítě)
update_check_hours: 12       # h, 0 = vypnuto: kontrola balíčků k aktualizaci a nové verze OpenWrt
backup_interval_days: 7      # dny, 0 = vypnuto: automatická záloha konfigurace každého zařízení
backup_keep: 10              # kolik záloh se drží pro každé zařízení
aps:                         # všechna spravovaná zařízení – aplikace používá jen tato
  - name: Router
    host: 192.168.1.1
    type: router             # jeden router: rozhraní, DHCP klienti, jména/IP klientů
  - name: AP-Living
    host: 192.168.1.2
    type: ap                 # výchozí
  - name: AP-Office
    host: 192.168.1.3
    port: 22                 # volitelné
switches:                    # volitelně: spravované switche čtené přes SNMP (jen čtení)
  - name: Switch-Core
    host: 192.168.1.5
    community: public        # read community SNMP v2c (výchozí public)
stats: true                  # stránka Statistiky (rychlosti, ping, kdo vytěžuje internet, porty switche)
stats_interval: 5            # s, 2–60: jak často se čtou router a čítače switchů
ping_targets:                # adresy v internetu, které router pinguje (IP nebo jméno)
  - name: Cloudflare
    host: 1.1.1.1
  - name: NIC.CZ
    host: nic.cz
ping_devices: true           # pingat i bránu poskytovatele, AP a switche
traffic_source: auto         # auto | nlbwmon | conntrack | off – kdo nejvíc vytěžuje internet
top_clients: 10              # 3–50
ha_sensors: false            # true = statistiky jako entity sensor.openwrt_admin_* v HA
ha_sensors_interval: 30      # s, 10–600
```

`dhcp_router` a `router_name` z verzí do 0.7.0 fungují dál, ale jsou zastaralé: když je `dhcp_router` vyplněný a žádné zařízení nemá `type: router`, router se stane zařízení se stejnou IP (nikdy se tedy nezobrazí dvakrát); jinak se router vytvoří z `dhcp_router`. Doporučeno: `dhcp_router` smazat a routeru dát `type: router`.

### SSH klíč

Při prvním startu si aplikace vygeneruje klíč `ed25519` (uložený v `/data`, je součástí záloh HA). Veřejnou část najdeš v aplikaci na stránce **Nastavení**. Přidej ji na každý AP i na router:

```sh
echo 'ssh-ed25519 AAAA… ha-openwrt-admin' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

`/etc/dropbear/authorized_keys` přežije běžný `sysupgrade`. Po `sysupgrade -n` ho přidej znovu, nebo ho dej do first-boot skriptu (uci-defaults).

> Klíč dává na zařízeních práva root a aplikace umí AP restartovat a měnit jeho konfiguraci. Kdo má přístup do `/data` aplikace (zálohy HA), má root na AP. **Zálohy HA šifruj.**

#### Doporučeno: vlastní uživatel místo `root`

Aby bylo v logu zařízení vidět, co dělala aplikace a co ty, ať se aplikace přihlašuje jako vlastní uživatel, např. `owadmin`, s právy root (UID 0). Nic se neinstaluje; na každém AP a routeru:

```sh
grep -q '^owadmin:' /etc/passwd || echo 'owadmin:x:0:0:OpenWrt-Admin:/root:/bin/ash' >> /etc/passwd
grep -q '^owadmin:' /etc/shadow || echo 'owadmin:*:0:0:99999:7:::' >> /etc/shadow
```

Pak v konfiguraci aplikace nastav `ssh_user: owadmin` (nebo `user: owadmin` jen u jednoho zařízení v `aps`). Stránka Nastavení ukáže příkazy i s tvým jménem uživatele.

- Uživatel nemá heslo (`*`), přihlásit se jde jen klíčem.
- Dropbear v OpenWrt bere klíče **všech uživatelů s UID 0** ze souboru `/etc/dropbear/authorized_keys`, klíč aplikace tedy zůstává tam (`~/.ssh` by se ignoroval). Stejný klíč by proto pořád fungoval i pro `root` – přínos je jasná stopa v logu, ne technické omezení. Uživatel bez práv root by potřeboval na každém zařízení nainstalovaný `sudo` a každý příkaz přes něj, čemuž se aplikace záměrně vyhýbá.
- `/etc/passwd` a `/etc/shadow` přežijí běžný `sysupgrade`; po `sysupgrade -n` řádky přidej znovu (first-boot skript).

#### Kdo co dělal (syslog zařízení)

Každou akci spuštěnou z aplikace (restart, změna SSID, nové SSID, nastavení rádií, `apk`, záloha, sken, odpojení/blokace klienta) aplikace zapíše i do syslogu zařízení, s tagem `openwrt-admin` a jménem uživatele Home Assistantu, který ji spustil; hesla jsou nahrazená `********`. Běžné čtení dat (každých pár sekund) se nezapisuje. Na zařízení:

```sh
logread -e openwrt-admin     # co se dělalo a kterým uživatelem HA
logread -e dropbear          # přihlášení (s vlastním uživatelem uvidíš „owadmin“ místo „root“)
```

### Co umí

| Akce | Kde | Příkaz na AP |
|---|---|---|
| Restart | stránka AP nebo routeru | `reboot` |
| Zkontrolovat aktualizace | stránka AP | `apk update && apk list --upgradable` (starší AP: `opkg`) |
| Upgrade balíčků | stránka AP | `apk upgrade` (starší AP: `opkg upgrade …`) |
| Název, heslo, skrytí, zapnutí SSID | stránka SSID | `uci set wireless.<sekce>.… && uci commit wireless && wifi reload` |
| Nové SSID na vybraných rádiích | stránka SSID → Přidat nové SSID | `uci set wireless.owadmin_<ssid>_<rádio>=wifi-iface` + device, mode, ssid, network, encryption, key → commit, `wifi reload` |
| Kanál, šířka, výkon, země rádií | stránka Rádia → Upravit rádia | `uci set wireless.<rádio>.channel/htmode/txpower/country` → commit, `wifi reload` |
| Záloha konfigurace | stránka AP nebo routeru | `sysupgrade -b /tmp/…` (archiv se přenese přes SSH a na zařízení smaže) |
| Odpojit / zablokovat Wi-Fi klienta | tabulky klientů (stránka AP, Klienti) | `ubus call hostapd.<if> del_client '{"addr":…,"deauth":true,"ban_time":…}'` |
| Spustit / zastavit / restartovat / zapnout / vypnout službu | stránka zařízení → Služby | `/etc/init.d/<služba> …` (`dropbear`, `network`, `rpcd`, `log`: jen restart, na pozadí) |
| Pozastavit / obnovit adblock, znovu načíst seznamy | stránka routeru → Služby → adblock | `/etc/init.d/adblock suspend` / `resume` / `reload`; pozastavení na čas: časovač na zařízení (`/tmp/owadmin-adb-resume`) |
| Odblokovat / zablokovat doménu (adblock) | stránka routeru → Služby → adblock | řádek v `/etc/adblock/adblock.allowlist` / `adblock.blocklist`, pak `adblock reload` |
| Odblokovat / zablokovat IP nebo síť (banIP) | stránka routeru → Služby → banIP | řádek v `/etc/banip/banip.allowlist` / `banip.blocklist`, pak `banip reload` (jen když je zapnutý) |
| Statická DHCP zápůjčka: přidat / upravit / smazat | stránka routeru → DHCP | `uci add dhcp host` / `uci set dhcp.<cfg…>.name/mac/ip/dns` / `uci delete dhcp.<cfg…>` → `uci commit dhcp`, `dnsmasq reload` (při chybě `uci revert`) |
| Rozsah DHCP, doba zápůjčky, zapnutí/vypnutí | stránka routeru → DHCP | `uci set dhcp.<síť>.start/limit/leasetime/ignore` → commit, `dnsmasq reload` |
| Místní jméno / alias: přidat / upravit / smazat | stránka routeru → DNS | `uci add dhcp domain` / `uci add dhcp cname`, `uci set …`, `uci delete …` → `uci commit dhcp`, `dnsmasq reload` |
| Nadřazené DNS / přesměrovaná doména v dnsmasq | stránka routeru → DNS | `uci add_list dhcp.<cfg…>.server=…`, `noresolv=1` → commit, `dnsmasq reload`, kontrola `nslookup nic.cz 127.0.0.1`, při chybě návrat |
| Nadřazené DNS na WAN | stránka routeru → DNS (červená sekce) | `uci add_list network.wan.dns=…` (`peerdns=0`) → commit, `network reload` na pozadí, kontrola, při chybě návrat |
| Zapnout / vypnout pravidlo firewallu nebo přesměrování | stránka routeru → Firewall | `uci set firewall.<cfg…>.enabled=0` / smazat → commit, `fw4 check`, `firewall reload` – s pojistkou (návrat po 90 s bez potvrzení) |

- **Přidání SSID:** název, heslo (8–63 znaků), šifrování WPA2 (`psk2`), WPA2/WPA3 (`sae-mixed`) nebo WPA3 (`sae`; u obou SAE se nastaví i `ieee80211w`), síť (rozhraní na AP, např. `lan` nebo VLAN síť) a skrytí; zaškrtnou se rádia (AP + pásmo). Síť musí na každém vybraném AP existovat a stejné SSID na tom rádiu ještě nesmí být. WPA3 potřebuje wpad s SAE (wpad-basic-mbedtls a novější).
- Úprava SSID mění **existující** SSID (sekce `wifi-iface`). Zaškrtnutím se vybírají AP a rádia (pásma), kterých se změna týká. Nejdřív se zobrazí náhled příkazů `uci`, zapíše se až po potvrzení. Heslo se nikde nezobrazuje.
- Restart a upgrade se potvrzují.
- **Upgrade balíčků** OpenWrt obecně nedoporučuje: nové verze jdou do overlay a na malých AP (8 MB flash) může místo dojít. Pro větší změny je lepší nový image a `sysupgrade`.

### Spravované switche (SNMP)

Do `switches` lze přidat spravované switche (Netgear GS7xxT, TP-Link, Cisco SG, Zyxel, HP…). Čtou se přes **SNMP v2c jen pro čtení** – na switchi se nic nemění ani neinstaluje. Používají se standardní MIB, takže funguje jakýkoli switch se SNMP:

| Data | MIB / OID |
|---|---|
| model, firmware, uptime | SNMPv2-MIB `system` |
| porty: stav, rychlost, duplex, název, popis | IF-MIB `ifOperStatus`, `ifHighSpeed`, `ifName`, `ifAlias`; EtherLike-MIB |
| MAC adresy na portech (po VLAN) | Q-BRIDGE-MIB `dot1qTpFdbPort` (záložně BRIDGE-MIB `dot1dTpFdbPort`) |
| VLANy na portech (netagované / tagované) | Q-BRIDGE-MIB `dot1qVlanStaticEgressPorts`, `…UntaggedPorts` |

- **Mapa:** switch je samostatný uzel. Jeho uplink je port, na kterém je MAC routeru; AP zapojená do switche se kreslí pod ním, každé s vlastní čárou (s rychlostí portu), kabelová zařízení na jeho portech jsou ve skupině **LAN**, obarvená podle sítě (LAN, IoT, DMZ…). Zařízení za AP zůstávají u toho AP (MAC patří nejhlubšímu zařízení, které ji vidí lokálně).
- **Stránka switche:** systém, panel **Porty** (všechny porty jako políčka; tabulka připojených portů s rychlostí, VLANy `U:`/`T:` a zařízeními) a diagnostika switche (tabulka MAC podle portů).
- **Nastavení SNMP na switchi** (Netgear GS724Tv4: System → SNMP → SNMP V1/V2 → Community Configuration): Management Station IP = adresa Home Assistantu (nebo LAN, např. `192.168.1.0` / `255.255.255.0`), Community String = vlastní community, Access Mode **Read Only**, Status **Enable**, pak **Add** (ne Apply).
- Test z libovolného Linuxu: `snmpwalk -v2c -c <community> <switch> 1.3.6.1.2.1.17.7.1.2.2.1.2`.
- LLDP není potřeba – stačí tabulka MAC adres.

### Porty a rychlost spojení

Stránka AP i routeru má panel **Porty**: každý fyzický port se stavem, vyjednanou rychlostí a duplexem, rolí a zařízeními, která jsou do něj zapojená (z tabulky MAC adres switche).

- Zařízení se **swconfig** (switch čipy jako Atheros/Qualcomm AR8xxx, MediaTek): porty switche z `swconfig dev <switch> show` (`link: port:N link:up speed:1000baseT full-duplex`). CPU port je samotné AP, uplink je port směrem k routeru. Zobrazují se jen porty použité ve `switch_vlan` (čip má často víc portů, než má zařízení zásuvek); bez `switch_vlan` se switch nepoužívá a ukážou se porty zařízení níže.
- Zařízení s **DSA** a bez switche (např. UniFi AP s jediným `eth0`, router se dvěma síťovkami): fyzická zařízení (`lan1`, `wan`, `eth0`…) z `ubus call network.device status` (`speed: "1000F"`). Ukážou se jen zařízení, která síť používá (členové bridge, zařízení rozhraní, `wan*`). Bez switche se za uplink bere jediný připojený port.
- Barvy: zeleně 1 Gb/s a víc, oranžově 100 Mb/s, červeně 10 Mb/s, čárkovaně = odpojeno; half-duplex je označený. **100 Mb/s místo 1 Gb/s** bývá poškozený nebo 4žilový kabel, případně zařízení, které víc neumí.
- Na **mapě** je u čáry ke každému AP rychlost jeho uplink portu a kabelová zařízení na portech AP mají rychlost svého portu (1 Gb/s zeleně, pod 1 Gb/s oranžově).

### Rádia a kanály

Stránka **Rádia** ukazuje všechna rádia všech AP v jedné tabulce a kontroluje nastavení:

| Sloupec | Zdroj |
|---|---|
| kanál (a frekvence), šířka, vysílací výkon, šum, čip | `iwinfo` – skutečné hodnoty; u kanálu `auto` ten, na kterém rádio opravdu vysílá, výkon po omezení hardwarem |
| nastavený kanál a výkon | `uci`/`network.wireless status` (ukáže se vedle skutečné hodnoty, když se liší) |
| vytížení kanálu teď, průměr a graf za poslední hodinu | `iw dev <if> survey dump` – `channel busy time` / `channel active time` mezi dvěma načteními AP (každých `poll_interval`) |

- **Upozornění a tipy:** dvě tvoje AP na stejném nebo překrývajícím se kanálu, 2,4 GHz mimo kanály 1/6/11, 40 MHz na 2,4 GHz, 5 GHz jen 20 MHz, kanál `auto`, DFS kanál (52–144), kanál vytížený nad 50 %, vysoký šum, 2,4 GHz vysílá silněji než 5 GHz na stejném AP (telefony se pak drží 2,4 GHz), různá nebo chybějící země.
- **Mapa kanálů** pro každé pásmo: které kanály každé AP zabírá i se šířkou; kanály 1/6/11 a DFS kanály jsou zvýrazněné.
- Stránka AP ukazuje v tabulce rádií skutečný kanál, šířku, výkon a vytížení kanálu.
- S `ha_sensors` má každé rádio i senzor `sensor.openwrt_admin_<ap>_<rádio>_channel_utilization` (%).
- **Sken okolí** (volba `radio_scan`, výchozí vypnuto, jen plný přístup): tlačítko **Skenovat** u každého rádia nebo pro všechna. Spustí `iwinfo <if> scan`; rádio na pár sekund přeskakuje po kanálech, připojení klienti můžou zaznamenat krátký výpadek. Výsledky zůstávají v paměti do restartu aplikace:
  - seznam okolních sítí pro každé rádio (SSID, BSSID, kanál, šířka i se sekundárním kanálem, signál, šifrování); vlastní AP se poznají podle BSSID (z `iwinfo`) nebo SSID a jsou označené,
  - sloupce obsazenosti kanálů pod mapou kanálů (počet cizích sítí na kanálu, výška podle síly signálu),
  - upozornění: silné cizí sítě (≥ −70 dBm) na tvém kanálu a návrh méně obsazeného kanálu (2,4 GHz: 1/6/11; 5 GHz: bloky v 36–48 pro tvou šířku – DFS a 149–165, které EU povoluje jen s nízkým výkonem, se nenavrhují).
- **Upravit rádia** (plný přístup): jeden formulář pro všechna rádia – kanál (povolené kanály z `iwinfo freqlist`, DFS označené, `auto`), šířka (z `iwinfo htmodelist`; starší iwinfo nevrací nic, pak podle rodiny rádia: rádio 802.11n nikdy nedostane VHT80), výkon (`iwinfo txpowerlist` nebo `auto`) a země pro všechna rádia. Změní se jen hodnoty, které se liší od současného nastavení. **Náhled změn** ukáže příkazy `uci` pro každé AP, **Použít** je zapíše (`uci commit wireless && wifi reload`, při chybě `uci revert wireless`). Klienti se na pár sekund odpojí; DFS kanál asi minutu nevysílá.
- **Navrhnout nastavení** předvyplní formulář: tvoje AP na různých kanálech (nejdřív vybírají rádia s nejvíc klienty), mimo kanály obsazené sousedy podle posledního skenu (2,4 GHz 1/6/11, 5 GHz 36–48; s *povolit DFS kanály* i 52–144), šířka na 2,4 GHz 20 MHz a na 5 GHz co nejširší, co rádio umí (max. 80 MHz), výkon: 5 GHz nejvyšší povolený, 2,4 GHz o 3 dB méně. U každé změny je důvod. Nic se nezapíše, dokud nedáš náhled a Použít. Bez skenu návrh jen rozdělí tvoje AP navzájem.

### Služby, adblock a banIP

Záložka **Služby** na stránce každého zařízení (router a AP: Přehled | Služby) ukazuje služby toho zařízení (`ubus call service list`, spouštění po startu z odkazů `S..` v `/etc/rc.d`): běží, dokončeno (jednorázové skripty jako adblock s kódem 0), zastaveno, selhalo (kód ≠ 0, když se má spouštět po startu), bez procesu (init skripty bez procd služby, např. `firewall`) a jestli se spouští po startu. Skripty, které jen něco nastaví při startu (`boot`, `sysctl`, `led`…), se neukazují. Stav se čte při otevření záložky (drží se 60 s, po každé akci znovu) – jen čtení, do logu zařízení se nic nezapisuje.

S plným přístupem: spustit, zastavit, restart, spouštět / nespouštět po startu. Každá akce nejdřív ukáže příkaz a provede se po **Použít**. `dropbear`, `network`, `rpcd`, `log`, `ubus`, `system` jdou jen restartovat, a to na pozadí za 2 s – bez nich by se aplikace na zařízení nedostala.

**adblock** (když existuje `/etc/init.d/adblock`): stav, počet blokovaných domén, seznamy, poslední běh a DNS backend.
- **Neblokuje** = adblock je zapnutý, ale blokovací seznam (`adb_list.overall`) je prázdný – typicky pozastavený z LuCI/příkazové řádky a zapomenutý, nebo se po startu routeru nestáhly seznamy. Pomůže **Obnovit blokování** nebo **Znovu načíst seznamy**.
- **Pozastavit** na 15 min, 1 h, 4 h nebo do obnovení. Pozastavení na čas obnoví časovač přímo na routeru (`sleep` na pozadí, hlídá `/tmp/owadmin-adb-resume`), takže funguje, i když se mezitím restartuje Home Assistant; restart routeru pozastavení zruší (adblock normálně naběhne). Stránka ukazuje, kdy se blokování obnoví, a upozorní, když čas uplynul a adblock je pořád pozastavený.
- **Proč je blokovaná?** spustí `/etc/init.d/adblock search <doména>` (ve starších verzích `query` – pozná se samo): aktivní seznam a zálohy jednotlivých seznamů (ve kterém seznamu doména je). Smí i uživatel jen pro čtení.
- **Odblokovat (povolit) / Zablokovat** přidá doménu do `/etc/adblock/adblock.allowlist` / `adblock.blocklist` (a smaže ji ze druhého seznamu), pak `adblock reload` (desítky sekund). Povolení platí přesně – `ads.example.com` nepovolí `x.ads.example.com`. Seznamy pod tím jde upravit křížkem ✕.

**banIP** (když existuje `/etc/init.d/banip`): stav (vypnuto / běží / neběží), počet prvků v sadách, seznamy. **Proč je blokovaná?** spustí `/etc/init.d/banip search <IP>` a ukáže sadu (např. `blocklistv4`, `cinsscorev4`). **Odblokovat / Zablokovat** pracuje s `/etc/banip/banip.allowlist` / `banip.blocklist` (IPv4/IPv6 adresa nebo síť CIDR); sady se znovu načtou jen když je banIP zapnutý. Samotný banIP (seznamy, země, rozhraní) se nastavuje v LuCI.

### DHCP (router)

Záložka **DHCP** na stránce routeru (Přehled | Služby | DHCP) pracuje s dnsmasq na routeru (`/etc/config/dhcp`):

- **Sítě a rozsahy** – u každé sítě s DHCP: podsíť, adresa routeru, dynamický rozsah (adresa sítě + `start` … + `start` + `limit` − 1, jako v LuCI), doba zápůjčky a obsazenost: statické zápůjčky v rozsahu, dynamické zápůjčky, volné adresy. S plným přístupem: úprava začátku, počtu, doby, zapnutí/vypnutí DHCP. Sítě se statickou adresou bez DHCP sekce jsou uvedené jako „Bez DHCP“ s možností DHCP zapnout. Když zbývá 5 a méně volných adres, objeví se upozornění.
- **Statické zápůjčky** (`dhcp.@host`) – jméno, MAC, IP, síť, „jméno do DNS“ (`dns=1`), stav online; hledání. Přidat / upravit / smazat. Navržená IP pro novou zápůjčku je první volná adresa **pod** dynamickým rozsahem (nemá ji jiná rezervace, zápůjčka, záznam v ARP ani místní jméno), aby rozsah zůstal volný pro ostatní zařízení.
  - Chyby změnu zastaví: neplatné jméno (písmena, číslice, pomlčka), MAC nebo IP, IP mimo sítě routeru, adresa sítě/broadcast/routeru, IP nebo MAC už v jiné rezervaci.
  - Varování se ukážou v náhledu: IP má teď zapůjčené jiné zařízení, leží v dynamickém rozsahu, používá ji místní jméno, nebo síť nemá DHCP.
- **Aktuální zápůjčky** (`/tmp/dhcp.leases`) se zbývajícím časem; **Udělat statickou** předvyplní formulář jménem, MAC a současnou IP.

Každá změna: náhled příkazů `uci` → **Použít** → `uci commit dhcp` a `/etc/init.d/dnsmasq reload` (DNS i DHCP běží dál); při chybě `uci revert dhcp`. Před první změnou dne aplikace udělá zálohu konfigurace routeru (stejně jako tlačítko Záloha). Anonymní sekce se čtou přes `uci -X` (jména jako `cfg0a1b2c`), takže úprava trefí správnou zápůjčku, i když mezitím přibyla jiná. Nová statická IP platí, až si zařízení obnoví zápůjčku. IPv6 (odhcpd) se nemění.

Záložka také upozorní na nesloučený `/etc/config/dhcp.apk-new` (nebo `dhcp-opkg`) – výchozí konfiguraci, která zůstala po aktualizaci balíčku; porovnej ji přes `diff -u` a smaž. Ostatní takové soubory (luci, uhttpd…) jsou v jednom sbaleném řádku – jsou neškodné, apk tvoji upravenou konfiguraci nikdy nepřepíše.

### DNS (router)

Záložka **DNS** na stránce routeru (Přehled | Služby | DHCP | DNS) pracuje se stejnou konfigurací dnsmasq (`/etc/config/dhcp`):

- **Test překladu** – spustí na routeru `nslookup <jméno> 127.0.0.1`, tedy se zeptá dnsmasq stejně jako klienti. Ukáže adresu (adresy), řetěz aliasu (alias → cíl → IP), **blokováno**, když je odpověď `0.0.0.0` (adblock), a **NXDOMAIN**, když jméno neexistuje; celý výstup je ve sbaleném řádku. Jen čtení, smí i uživatel jen pro čtení.
- **Místní jména** (`dhcp.@domain`, v LuCI „Hostnames“) – jméno → IPv4/IPv6 adresa; přidat, upravit, smazat, hledat. Jméno bez domény (`grafana`) se přeloží i jako `grafana.<místní doména>`; funguje i jméno ve vlastní doméně (`server.home`) nebo veřejná doména, kterou chceš doma posílat na vnitřní adresu (split DNS, např. web server v DMZ). Chyby: neplatné jméno nebo adresa, stejné jméno se stejnou adresou dvakrát, jméno, které už je alias. Varování: jméno už má jinou adresu (bude mít dvě), adresa je mimo sítě routeru.
- **Aliasy** (`dhcp.@cname`) – alias → cílové jméno; přidat, upravit, smazat. dnsmasq alias vrátí jen tehdy, když cíl zná sám (místní jméno, statická nebo aktuální zápůjčka) – jinak náhled varuje a v seznamu je „?“.
- **Nadřazené servery** – panel ukazuje, které servery se teď používají (z WAN přes `resolv.conf.auto`, nebo z dnsmasq, když je nastavené `noresolv`). Změnit je jde dvěma způsoby:
  - **Servery v dnsmasq (doporučeno)** – seznam `server` v dnsmasq s `noresolv=1`; má přednost před servery z WAN a mění se jen reloadem dnsmasq, bez výpadku internetu. Prázdné pole = zpět na servery z WAN. Předvolby: CZ.NIC ODVR, Cloudflare, Quad9, Google. Tady se přidávají a mažou i **přesměrované domény** (`server=/doména/ip`, např. firemní doména za VPN).
  - **Servery na rozhraní WAN** (červená sekce) – `network.wan.dns` (u WAN přes DHCP/PPPoE i `peerdns=0`); reload sítě, internet může na pár sekund vypadnout. Skript běží na routeru na pozadí, aby ho odpojení SSH nepřerušilo uprostřed. Dokud má dnsmasq vlastní servery, servery z WAN se nepoužijí (náhled upozorní).
  - Pojistky: před uložením se router zeptá každého nového serveru (`nslookup nic.cz <server>`) – neodpovídající označí, a když neodpoví žádný, změnu nepovolí. Po uložení router přeloží `nic.cz` přes sebe (několik pokusů); když to nejde, sám vrátí původní nastavení (servery, `noresolv`, `peerdns`). Servery s vlastním portem (`127.0.0.1#5053`, lokální DoH/DoT proxy) se předem netestují.
- **Nastavení DNS** (jen čtení) – místní doména, kolik statických zápůjček dává jméno do DNS (`dns=1`), ochrana proti rebind a její výjimky.

Změny fungují stejně jako u DHCP: náhled → **Použít** → `uci commit dhcp` + `/etc/init.d/dnsmasq reload`, při chybě `uci revert dhcp`, záloha konfigurace před první změnou dne, záznam do syslogu se jménem uživatele HA. Klienti můžou mít starou odpověď chvíli v mezipaměti; test překladu ukáže odpověď routeru hned.

### Firewall (router)

Záložka **Firewall** na stránce routeru čte fw4 (na fw3 – OpenWrt 21.02 a starší – ukáže omezený přehled jen pro čtení): `uci show firewall`, skutečné pořadí zón na rozhraních z `fw4 print` (která zóna dostane provoz z rozhraní jako první) služby, které na routeru poslouchají, z `netstat` (nebo `ss`) a rozhraní WAN podle výchozí cesty (`ip route`), takže funguje, ať se WAN jmenuje jakkoli.

- **Kontroly** nahoře: rozhraní WAN v zóně se vstupem ACCEPT (router je z internetu dostupný kromě toho, co blokuje pravidlo – otevřené služby se vypíšou), zóna WAN s průchodem ACCEPT, síť nebo rozhraní ve více zónách (platí jen první, pravidla ostatních ne), WAN bez maškarády, staré include z fw3 (`/etc/firewall.user`), které fw4 ignoruje.
- **Co je otevřené z internetu** – přesměrování portů (`redirect` z WAN), pravidla ACCEPT z WAN a služby na routeru se stavem: otevřená (nic ji neblokuje), povolená pravidlem, blokovaná pravidlem, port přesměrovaný jinam (DNAT má přednost), zavřená politikou zóny. Celý seznam poslouchajících služeb je ve sbalené části.
- **Mapa zón** – řádky = odkud, sloupce = kam; sloupec „router“ je vstup na samotný router (politika input zóny), ostatní sloupce průchody mezi zónami, na diagonále politika forward uvnitř zóny. „+ N pravidel“ = výjimky pro konkrétní porty nebo adresy.
- **Přesměrování portů** a **pravidla** v pořadí konfigurace (první shoda vyhrává) – zóny, protokol, porty, zdrojová IP/MAC, časové podmínky; pravidla, která zahazují provoz konkrétních zařízení, mají štítek „blokace“. Vlastní soubory v `/etc/nftables.d/` se jen vypíšou.

S plným přístupem jde existující pravidla a přesměrování **zapnout a vypnout** (zapnutí přesměrování upozorní, že otevře port). Další úpravy (blokování zařízení i na čas, přesměrování portů, pravidla) přijdou v dalších verzích.

**Pojistka při ukládání.** Změny firewallu (a později sítí a VLAN) se ukládají jako „apply with rollback“ v LuCI: zařízení si zazálohuje dotčenou konfiguraci do `/tmp/owadmin-rb/<token>/`, spustí odpočet 90 s, změnu provede na pozadí (aby ji nepřerušilo odpojení SSH) a spustí `fw4 check` – když kontrola selže, záloha se vrátí hned. Aplikace pak otevře nové SSH spojení a změnu potvrdí. Když se připojit nejde (změna ji odřízla), zařízení po 90 s samo vrátí zálohu, znovu načte a zapíše do logu `rollback … not confirmed`. Před první změnou dne se konfigurace zálohuje jako u DHCP.

### Statistiky

Stránka **Statistiky** (volba `stats`, ve výchozím stavu zapnutá) ukazuje, co síť dělá teď a za poslední hodinu:

| Panel | Zdroj |
|---|---|
| stahování/odesílání WAN, rozhraní (WAN, LAN, každá VLAN, tunely): RX/TX, pakety/s, špička, graf za poslední hodinu | router `/proc/net/dev` |
| teplota CPU a zátěž routeru | `/sys/class/thermal/thermal_zone*/temp`, `/proc/loadavg` |
| ping: cíle v internetu (`ping_targets`), brána poskytovatele, AP a switche – poslední/průměrná odezva, ztráta, pruh historie | `ping -c 1 -W 1` na routeru; router samotný = odezva SSH z aplikace |
| kdo nejvíc vytěžuje internet (top `top_clients`) | **nlbwmon** (`nlbw -c json -g mac,ip`), když na routeru běží, jinak **conntrack** (`/proc/net/nf_conntrack`) |
| aktuální provoz Wi-Fi klientů | rozdíl `bytes` z hostapd mezi dvěma načteními AP |
| nejvytíženější porty switche – i provoz mezi zařízeními uvnitř sítě (např. notebook ↔ server), který neprochází routerem | SNMP IF-MIB `ifHCInOctets`/`ifHCOutOctets` (záložně 32bitové `ifInOctets`/`ifOutOctets`) |

- Router a switche se čtou každých `stats_interval` sekund (výchozí 5) přes **jedno trvalé SSH spojení** (žádné nové přihlášení pokaždé, nic v logu routeru). AP se dál čtou podle `poll_interval`.
- Na zařízeních se nic neinstaluje. `nlbwmon` se použije jen tehdy, když už na routeru je; `traffic_source` umí vynutit `nlbwmon`, `conntrack` nebo to vypnout (`off`).
  - **nlbwmon** obnovuje data po svém `refresh_interval` (výchozí 30 s), rychlost je proto průměr za ~90 s. Navíc dává součet za aktuální účetní období.
  - **conntrack** potřebuje `net.netfilter.nf_conntrack_acct=1` (výchozí v OpenWrt; ověření `cat /proc/sys/net/netfilter/nf_conntrack_acct`). Počítá se jen IPv4 provoz mezi LAN/VLAN a internetem, včetně přesměrovaných portů. Se zapnutým **flow offloadingem** jdou pakety mimo conntrack a část provozu chybí.
- Cíle pingu musí být IP adresa nebo jméno; nic jiného se nepinguje (žádné skenování).
- Historie (1 hodina) je jen v paměti a po restartu aplikace zmizí. Na dlouhodobou historii zapni `ha_sensors`.
- **Mapa:** čáry k AP a switchům ukazují aktuální provoz na jejich přívodu (`↓` k zařízení / `↑` od něj) z čítačů portů switche, čára z internetu provoz WAN, kabelová zařízení na portech switche provoz svého portu. Nad polovinou rychlosti portu červeně a silně.
- **Stránka switche** má v tabulce portů sloupec *Provoz*, **stránka routeru** ukazuje u každého rozhraní RX/TX teď, tabulky **Klienti** mají sloupec *Teď ↓/↑* a přehled dlaždice *Internet* a *WAN*.

#### Senzory v Home Assistantu

S `ha_sensors: true` aplikace každých `ha_sensors_interval` sekund zapisuje statistiky do Home Assistantu přes jeho REST API (`homeassistant_api`, MQTT není potřeba):

| Entita | Stav |
|---|---|
| `sensor.openwrt_admin_<router>_<rozhraní>_rx` / `_tx` | Mbit/s (`device_class: data_rate`) |
| `sensor.openwrt_admin_<router>_<rozhraní>_packets` | pakety/s |
| `sensor.openwrt_admin_<router>_cpu_temperature`, `…_load` | °C, zátěž |
| `sensor.openwrt_admin_ping_<jméno>` / `binary_sensor.openwrt_admin_ping_<jméno>` | odezva v ms (atribut `loss_1h`) / dostupnost on/off |
| `binary_sensor.openwrt_admin_internet` | on, když odpovídá aspoň jeden cíl v internetu |
| `sensor.openwrt_admin_top_clients` | počet aktivních klientů; atribut `clients` = seznam (jméno, IP, MAC, download, upload v Mbit/s) |
| `sensor.openwrt_admin_<switch>_port<N>_down` / `_up` | Mbit/s, jen připojené porty |
| `sensor.openwrt_admin_<ap>_<rádio>_channel_utilization` | % (atributy kanál, htmode, výkon, šum) |

Entity vzniknou při prvním zápisu. Nemají `unique_id`, takže je nejde přejmenovat v UI, a po restartu HA se objeví znovu s další aktualizací. Mají `state_class: measurement`, takže HA drží dlouhodobé statistiky a jdou do InfluxDB a do **Grafany**. Stránka Nastavení ukazuje, kolik entit se posílá, a poslední chybu.

### Nová zařízení v síti

Když se v síti objeví zařízení (MAC adresa), které aplikace ještě nikdy neviděla – na Wi-Fi, na portu AP nebo v ARP tabulce routeru – pošle upozornění do Home Assistantu (zvoneček, `persistent_notification`). Je v něm jméno (pokud je známé), MAC, IP, síť/VLAN a kde zařízení je (AP + SSID, port AP, router). S `notify_service` (např. `notify.mobile_app_telefon`) jde upozornění i do té služby.

- Při **prvním spuštění** se všechna zařízení, která jsou zrovna v síti, berou jako známá – žádná záplava upozornění. Seznam je v `/data/known_devices.json` (smazáním souboru se začne znovu).
- Zařízení poprvé viděná za posledních `new_device_hours` hodin (výchozí 24, 0 = vypnuto) mají v tabulkách klientů a na stránce routeru štítek **nové**.
- Telefony používají pro každou Wi-Fi **náhodnou (soukromou) MAC**; takové adresy jsou v upozornění označené. `notify_random_mac: false` je vynechá.
- Samotné AP a router se nehlásí.

### Aktualizace

Každých `update_check_hours` (výchozí 12) spustí aplikace na každém zařízení postupně `apk update` + `apk list --upgradable` (starší zařízení `opkg update` + `opkg list-upgradable`). Přehled ukazuje počet balíčků k aktualizaci (dlaždice a na každé kartě), stránka zařízení je vypíše s nainstalovanou a novou verzí. Tlačítko **Zkontrolovat aktualizace** seznam hned obnoví; po **Upgrade balíčků** se zkontroluje znovu.

Nová **verze OpenWrt** se zjišťuje z `https://downloads.openwrt.org/.versions.json` (HA potřebuje internet): přednost má novější verze stejné řady (např. 25.12.2 → 25.12.3), jinak novější stabilní řada. Firmware aplikace neaktualizuje – na to firmware-selector a `sysupgrade`.

### Zálohy konfigurace

Tlačítko **Zálohovat teď** na stránce AP nebo routeru (plný přístup) a jednou za `backup_interval_days` (výchozí 7) automatická záloha spustí na zařízení `sysupgrade -b`. Archiv se přenese přes SSH a uloží do `/data/backups/<zařízení>/<zařízení>-RRRR-mm-dd_HHMMSS.tar.gz` aplikace; drží se posledních `backup_keep` (výchozí 10). `/data` je součástí záloh Home Assistantu, takže konfigurace AP se zálohují spolu s HA.

- Archiv obsahuje **hesla Wi-Fi a klíče** – stáhnout ho může jen uživatel s plným přístupem (odkaz v seznamu záloh).
- **Obnova:** soubor nahrát na zařízení (např. `scp soubor.tar.gz root@ap:/tmp/`) a spustit `sysupgrade -r /tmp/soubor.tar.gz && reboot`, nebo LuCI → Systém → Zálohování / Flash firmware → Obnovit. Aplikace sama neobnovuje.
- Které soubory se zálohují, určuje na zařízení `/etc/sysupgrade.conf` (stejně jako pro `sysupgrade`).

### Odpojení a blokace Wi-Fi klientů

V tabulkách klientů (stránka AP, **Klienti**) mají uživatelé s plným přístupem nabídku **Odpojit…**:
- **jen odpojit** – klient se odpojí (deautentizace) a obvykle se hned připojí znovu; hodí se, aby přešel na bližší AP nebo aby se zaseklé zařízení znovu připojilo,
- **+ blokovat 10 min / 1 h / 24 h** – odpojí a po tu dobu nepustí zpět na **všech AP, která vysílají stejné SSID** (jinak by se připojil k jinému AP).

Blokaci drží `hostapd` v paměti: restart Wi-Fi (`wifi reload`, změna SSID) nebo restart AP ji zruší. Trvalé blokování je MAC filtr (`macfilter`/`maclist`) v nastavení SSID – to aplikace nedělá.

Zablokovaní klienti jsou vypsaní nad tabulkou klientů (stránka Klienti, stránka AP) s AP a zbývajícím časem; **Zrušit blokaci** ji zruší na všech AP s tím SSID (`del_client` s `ban_time: 0`). Seznam je z `ubus call hostapd.<if> list_bans`; u staršího hostapd bez této metody se ukazují jen blokace zadané z aplikace.

### Uživatelé a oprávnění

Přihlášení řeší Home Assistant (Ingress), aplikace vlastní hesla nemá. Podle uživatele HA, který panel otevřel, přidělí roli:

| Role | Kdo | Co smí |
|---|---|---|
| plný přístup | je v `full_access` | vše: restart, balíčky, změny SSID, zálohy (i stažení), odpojení klientů |
| jen čtení | je v `read_access` | přehled, klienti, SSID, detail AP, seznam aktualizací a záloh – bez tlačítek akcí, bez stažení záloh |
| bez přístupu | není v žádném seznamu | stránka „Přístup odepřen“ |

- Píše se **uživatelské jméno** HA (Nastavení → Lidé → Uživatelé) nebo zobrazované jméno, velikost písmen nehraje roli.
- **Oba seznamy prázdné:** plný přístup má každý, kdo panel otevře. Stránka Nastavení na to upozorní.
- **Ingress nepošle jméno uživatele:** jen čtení, nikdy plný přístup.
- Kontroluje se na serveru, ne jen v zobrazení. Akce se logují se jménem uživatele.
- Panel vidí všichni uživatelé HA (`panel_admin: false`). Samotné schování panelu by nestačilo, HA ingress adresu u běžného uživatele neblokuje.

### Jména zařízení

Zařízení s pevnou IP (typicky ESPHome) o DHCP nežádají, takže router jejich jméno nezná. Aplikace proto skládá jména z více zdrojů v tomto pořadí:

1. **`names`** v konfiguraci aplikace (ručně, štítek *ručně*),
2. **Home Assistant** – registr zařízení podle MAC (ESPHome, Shelly…; štítek *HA*). Aplikace má `homeassistant_api: true` a každých 10 minut čte `config/device_registry/list` přes websocket Supervisoru; vypnout jde přes `ha_names: false`,
3. **DHCP** na routeru,
4. **statická rezervace DHCP** na routeru (`uci show dhcp`, sekce `host`; štítek *rezervace*).

IP se bere z DHCP, z rezervace, nebo z ARP tabulek routeru a AP (označeno *pevná IP*). Stránka routeru ukazuje všechna zařízení, která router zná (DHCP, rezervace nebo ARP); ta, která teď v síti nejsou, jsou ve sbalené sekci **Neaktivní**. Zařízení je přítomné, když je Wi-Fi klientem, je vidět na portu switche AP, nebo má kompletní záznam v ARP tabulce routeru či AP – samotná rezervace nebo starý DHCP záznam nestačí. Mapa ukazuje jen přítomná zařízení. Na stránce Nastavení je vidět, kolik jmen přišlo z kterého zdroje.

### Router

Router (zařízení s `type: router`) je samostatné zařízení, bez rádií a SSID. Má vlastní záložku, kartu na přehledu a stránku s:
- informacemi o systému (model, firmware, uptime, load, RAM, overlay),
- **rozhraními** z `ubus call network.interface dump`: WAN/LAN/VLANy, stav, protokol, IPv4/IPv6, brána, jak dlouho běží,
- **DHCP klienty**: všechna zařízení, která mají IP od routeru; Wi-Fi klienti s AP/SSID/pásmem, ostatní jako kabel nebo nepřipojeno k Wi-Fi,
- **sítí** každého zařízení (LAN / VLAN a název rozhraní, podle podsítě, do které patří jeho IP) a počtem aktivních zařízení u každého rozhraní,
- stejnými akcemi jako AP: restart, kontrola aktualizací, upgrade balíčků. Restart routeru na chvíli odpojí celou síť i internet.

SSH klíč aplikace musí být i na routeru.

Záznamy ARP na WAN a na tunelových/VPN rozhraních (WireGuard `wg*`, PPPoE, OpenVPN…) se ignorují – brána poskytovatele ani VPN protějšky nejsou klienti.

### Mapa sítě

Stránka **Mapa** kreslí síť jako strom: Internet → router → access pointy → SSID → **klienti**, k tomu uzel pro DHCP klienty na kabelu a ostatní se zařízeními pod ním. Každý uzel je odkaz na svou stránku (SSID na vyfiltrovaný seznam klientů); po najetí na klienta se ukáže IP, MAC, pásmo a signál.

- **Klienti** jsou se jménem (nebo MAC) a signálem (zeleně ≥ −60 dBm, oranžově ≥ −72 dBm, červeně slabší); zařízení na kabelu s IP. Přepínač **Klienti** je vypne (SSID pak ukazují jen počet).
- **SSID bez klientů se skrývají**, přepínač **SSID bez klientů** je zobrazí.
- **Zoom a posun:** kolečko myši přibližuje kolem kurzoru, tažením se mapa posouvá, tlačítka **+ / − / Vše**. Pozice zůstane i po automatickém obnovení stránky.
- **AP jako switch:** zařízení zapojená kabelem do portů AP se ukážou pod tím AP ve skupině **LAN** s číslem portu a AP zapojený přes jiný AP se nakreslí pod ním (lomená čára). Zdroj: tabulka MAC adres switche na každém AP (`swconfig dev switch0 get arl_table`); uplink je port, na kterém je MAC routeru (z ARP tabulky AP), CPU port je ten tagovaný ve všech `switch_vlan`. Když stejnou MAC vidí víc AP zapojených za sebou, patří poslednímu. Kabelová zařízení, která nejsou na portu žádného AP, zůstávají u routeru **rozdělená podle sítě**: **LAN / ostatní** pro netagovanou LAN a pro každou VLAN vlastní větev (např. **VLAN 100 · dmz**) v barvě té sítě. Síť je rozhraní routeru, do jehož podsítě patří IP zařízení. Stránka routeru u každého DHCP klienta ukazuje, jestli je na Wi-Fi, na portu AP, nebo jinde.
- **Poslední známé místo:** switch MAC po pár minutách bez provozu zapomene (např. vypnutá televize). Aplikace si pamatuje, kde bylo každé kabelové zařízení naposledy vidět (AP + port, v `/data/wired_seen.json`), a nechá ho tam šedě s „naposledy viděno …“, dokud má na routeru DHCP záznam (bez záznamu 24 hodin). Záznamy starší než 30 dní se mažou.
- **Tichá zařízení:** zařízení, které skoro nic nevysílá (např. IP telefon), vypadne z tabulky switche dřív, než ho aplikace uvidí. Router proto jednou za 4 minuty pingne kabelová zařízení s neznámým místem (každé jedním `ping -c1 -W1`, nejvýš 64), switch AP se je znovu naučí a při dalším načtení se umístí.
- **Diagnostika switche** (sbalená sekce dole na stránce AP): každý port switche s MAC adresami a rolí (uplink, CPU, lokální), jestli AP vidí router ve své ARP tabulce, a paměť „naposledy viděno“ pro ten AP. Hodí se, když je zařízení na mapě jinde, než má být.
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

Otevřené stránky se obnovují po `refresh_interval` sekundách (0 = vypnuto). Každý prohlížeč si může nastavit vlastní hodnotu na stránce **Nastavení** aplikace (uloží se do cookie). Stránka SSID se sama neobnovuje, aby se neztratil rozepsaný formulář.

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
| buzení tichých kabelových zařízení (router, každé 4 min) | `ping -c1 -W1 <ip>` |
| balíčky k aktualizaci (každých `update_check_hours`) | `apk update -q; apk list --upgradable` / `opkg update; opkg list-upgradable` |
| rychlost portů | `swconfig dev <switch> show`, `ubus call network.device status` |
| záloha konfigurace | `sysupgrade -b` |
| rozhraní a VLANy (router, AP) | `ubus call network.interface dump`, `ubus call network.device status` |
| jména a IP klientů (router) | `ubus call luci-rpc getDHCPLeases`, jinak `/tmp/dhcp.leases`; `uci show dhcp` (rezervace), `/proc/net/arp` |
| jména zařízení (Home Assistant) | websocket `config/device_registry/list` (MAC v connections) |
| statistiky (router, každých `stats_interval`) | `/proc/net/dev`, `/sys/class/thermal`, `/proc/loadavg`, `ping -c 1 -W 1`, `nlbw -c json -g mac,ip` nebo `/proc/net/nf_conntrack` |
| provoz na portech switche | SNMP `ifHCInOctets`, `ifHCOutOctets` |
| rádia: skutečný kanál, šířka, výkon, šum; vytížení kanálu | `iwinfo`, `iw dev <if> survey dump` |
| okolní sítě (jen po Skenovat, `radio_scan`) | `iwinfo <if> scan` |
| služby, adblock, banIP (jen při otevření záložky Služby) | `ubus call service list`, `ls /etc/init.d /etc/rc.d`, `/etc/init.d/adblock status`, `/etc/init.d/banip status`, soubory povolených/blokovaných |
| DHCP a DNS (jen při otevření záložky DHCP nebo DNS; test překladu: `nslookup <jméno> 127.0.0.1`) | `uci -X show dhcp`, `uci show network` (proto/ipaddr/netmask), `/tmp/dhcp.leases`, `resolv.conf.auto`, `ls /etc/config/*.apk-new` |
| firewall (jen při otevření záložky Firewall) | `uci -X show firewall`, `fw4 print` (pořadí zón na rozhraních), `netstat -tulpn` / `ss -tulpn`, `ip route` + `ip addr` (WAN), `ls /etc/nftables.d` |

Funguje i na AP bez LuCI (stačí `wpad`/`hostapd` a `ubus`, které jsou v každém OpenWrt).

### JSON API

`…/api/state` vrací celý stav (AP + klienti) jako JSON, `…/api/stats` aktuální statistiky (rozhraní, ping, top klienti, provoz Wi-Fi, porty switche).

### Jazyky

- **Webové rozhraní:** angličtina a čeština. Jazyk se vybírá v tomto pořadí: volba na stránce **Nastavení** aplikace (pro daný prohlížeč) → `language` v konfiguraci (pokud není `auto`) → jazyk uživatele Home Assistantu → jazyk prohlížeče → angličtina.
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
| Switch: `SnmpError: SNMP timeout` | Špatná IP nebo community, SNMP není zapnuté, nebo adresa Home Assistantu není povolená (Management Station IP). |
| Nepřišlo upozornění na nové zařízení | `notify_new_devices` vypnuté, zařízení už bylo známé (první spuštění = výchozí stav), nebo selhalo volání HA (chybu ukáže stránka Nastavení). |
| Záloha selže | Na zařízení chybí `sysupgrade -b` nebo je plné `/tmp`; chyba se ukáže po **Zálohovat teď** a v logu aplikace. |
| Kabelové zařízení u routeru místo u svého AP | Stránka AP → **Diagnostika switche**: je poznaný uplink, je MAC na lokálním portu, je v „naposledy viděno“? Tichá zařízení se najdou do pár minut (ping z routeru). |
| „Přístup odepřen“ | Uživatel HA není v `full_access` ani `read_access`. |
| Všichni mají jen čtení | Ingress neposílá jméno uživatele (Nastavení ukáže „Přihlášen jako –“), nebo jméno neodpovídá uživatelskému jménu HA. |
| Aplikace nenastartuje, `python-multipart` | Stará verze 0.2.0, aktualizovat. |
| Repozitář nejde přidat | Špatné místo (Marketplace), HA nevidí Git server (zkusit IP), špatný token. Detail: Nastavení → Systém → Protokoly → **Supervisor**. |

Logy: karta **Protokol** u aplikace.
