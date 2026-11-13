# 10 — T‑Box & telematics

Two questions were chased here: *(1) can the head unit get general internet through the car's
T‑Box?* and *(2) what does the car phone home, and can it be blocked?* Short answers: **no** (dead
SIM, no software lever), and **partly** (the IVI‑side reporter is blockable, the T‑Box firmware
reporter is not).

## The T‑Box

The telematics unit is a **separate Neusoft box** with its own modem/SIM; the SA8155's own modem is
unused (`ro.baseband=unknown`, telephony `OUT_OF_SERVICE`).

| Property | Value |
|---|---|
| `sys.vehicle.tbox.hw.version` | `CNQ1H60EUR` |
| `sys.vehicle.tbox.sw.version` | `FCOT19Z…` *(redacted)* |
| serial | *(redacted)* |
| `tbox.sim.state` | `false` |
| `signal.strength` | `-1` |
| `network.type` | `NoNetwork` |
| stored SIM | non‑geographic **M2M/IoT** profile (IMSI `901312…` MCC 901, ICCID `8988301…`) |

## Internet‑via‑T‑Box: **blocked** (and why)

The path *physically exists and is the active default route*: IVI `usb0` = `192.168.225.10/24` →
gateway `192.168.225.1` (T‑Box RNDIS/NAT), transport CELLULAR, INTERNET capability. There's even an
in‑car Wi‑Fi hotspot (`persist.sys.gac.wifi_ap_name` / `_password`, redacted) that would auto‑share
T‑Box data once an uplink exists.

**But the T‑Box has no cellular service** (`sim.state=false`), and decompiling `SVTsp` +
`SVTBoxInfo` showed the *entire* T‑Box VDBus vocabulary is **status/telematics only** —
`TBOX_NETWORK_STATUS`, `TBOX_SIM_INFO`, `TBOX_CONNECT_STATUS`, `TBOX_LOC_INFO`, `TBOX_VIN_INFO`,
`TBOX_PKI_INFO`, `TBOX_CALL_*`, `TBOX_FOTA_*`, `TBOX_REMOTE_CONTROL_INFO`, `TBOX_RPA_INFO`. **There
is no data‑enable, APN, hotspot, data‑switch or dial command** anywhere, not even via the untrusted
VDBus route ([04](04-vdbus-untrusted-access.md)). The T‑Box manages its cellular autonomously; the
IVI only *observes*. The T‑Box is also a sealed NAT box: only port 53 (DNS) open, no AT/diagnostic
TCP port.

**Conclusion:** the only path to "internet through the T‑Box" is giving the T‑Box a working data
subscription — a **hardware/SIM operation on the T‑Box itself**. Evidence converges on the factory
SIM being an OEM‑managed **eUICC/MFF2** (soldered, provider‑locked M2M), so swapping it is almost
certainly not bench‑viable; only a physical teardown could prove otherwise. The working real‑world
answer for in‑car internet is a **phone/router Wi‑Fi hotspot** (verified: gives the IVI full
validated internet and reaches all Chery backends), or an aftermarket SIM‑slot T‑Box.

## Phone‑home map (Chery TSP / PKI control plane)

Decompiled from `SVTsp` + the shared net lib. There are **two independent reporters**:

### Reporter A — IVI‑side (`com.desaysv.tsp`, `com.chery.auto.tsp.sdk.*`) — **blockable**

- Regional SDK variants (russia + saudi). Transport includes **Huawei cloud MQTT/push**.
- Uploads: `ReportTrackEventRequest` → `TrackEvent{event_id, event_name, properties, timestamp,
  device_id, user_id, app_id}` (per‑user product/behavior analytics); `ReportDeviceInfoRequest`;
  account plumbing (login/token/QR pairing/SIM query/privacy‑sign).
- The Russia variant has consent ops (`SetPrivacy`, `QueryConformance`/`SetConformance`,
  `SwitchOperate`) — the same consent that activates connected services also turns this on.

### Reporter B — T‑Box firmware (inside the sealed unit) — **not blockable from Android**

Vehicle telematics (GPS, status, remote control, eCall MSD) leaves the T‑Box modem directly; never
traverses the IVI. Inbound remote‑control set `VDRemoteControlInfo` (cloud→T‑Box→IVI/MCU/QNX)
includes remote EV charge scheduling, remote ECU diagnostics (read DTCs/versions across domains),
light‑show control, and `upLogUrl` — **the cloud can command the car to upload logs to an arbitrary
URL** (a designed exfil path).

### Backends (public endpoints, DNS/TLS‑probed — named, not touched)

- **TSP terminal:** `tspterminal.chery.ru` (→ 87.242.95.67, cert `CN=tspterminal.chery.ru, O=Chery,
  Shanghai`), `tspterminal.chery.casia`, `tspterminal-eu.cheryinternational.com` (Alibaba Cloud NLB
  eu‑central‑1), + uat/stg variants on `lionaitech.com` (vendor **Lion AI Tech**).
- **PKI gateway (mTLS :6443):** `pkipgw.chery.ru:6443` (behind SberCloud WAF),
  `pkipgw-{eu,sa,casia}.cheryinternational.com:6443` (Alibaba WAF, cert `O=奇瑞汽车股份有限公司`).
- **Account / connected‑car gateway:** `cainternational.chery.ru`,
  `cainternational-{eu,ar}.cheryinternational.com`.
- **FOTA/OTA API:** `https://fotaapis-eu.cheryinternational.com:443` (requires device mutual‑TLS).
- The **Huawei MQTT broker is runtime‑provisioned** in the TSP login response — no static hostname
  to blocklist; block the HTTP gateways that provision it and the push never establishes.

### Blocking summary (minimize phone‑home)

- **Reporter A:** `pm disable-user --user 0 com.desaysv.tsp` + an on‑device VpnService firewall
  (NetGuard/RethinkDNS, no root) blocking `tspterminal.chery.*`, `tspterminal*.cheryinternational
  .com`, `cainternational.*`, `pkipgw*.chery*`, `*.cheryinternational.com` PKI, `*.lionaitech.com`.
  Blocking the TSP terminal + `cainternational` gateways transitively kills the Huawei MQTT push.
- **Reporter B:** not blockable from Android — only carrier/APN egress filtering, a physical
  interposer, or leaving the T‑Box offline. (On this car it's already offline — dead SIM.)
- **eCall/SOS:** leave alone (safety; works even on an unprovisioned SIM via the emergency bearer).

## OTA: online update not fetchable

`SVVDSUpgrade` + the T‑Box do OTA; the online version negotiation is **T‑Box↔cloud**, gated by
**mutual‑TLS (device client cert in the T‑Box) + private `*.cadp` DNS**. With the T‑Box SIM dead,
the car makes no online FOTA request to spoof, and the path is cert‑gated anyway. Firmware
acquisition = **dealer / local USB `.cbf`** only (signed, verified by
`security_upgrade_public_key.txt`). The sideloaded `com.carota.chery` agent is a foreign
Jaecoo/Omoda app and is non‑functional on this unit.
