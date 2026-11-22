# 00 — Safety, scope & legal

## Scope

This repository documents reverse‑engineering of a **Chery Tiggo 4 (2025)** head unit that the
author owns. It is published for research, repair, interoperability and personal‑customization
purposes. It is a set of *notes about how the device works* — not a service you attack, and not a
tool aimed at anyone else's vehicle.

## What this is **not**

- Not a remote internet exploit. The Chery TSP/PKI backends are only *named* here
  (they are already public endpoints); nothing here authenticates to them or tries to attack cloud services.
- Not a way to steal, unlock or remote‑start someone else's car over the air. Physical/local
  access (USB port, local Wi-Fi, or vehicle network) is required for all access paths documented here.

## Privilege escalation & root access

- **Initial ADB access:** The USB signature method documented in [02](02-adb-access.md) re‑enables
  the OEM factory debug interface, granting standard Android unprivileged shell (`uid=2000`, `shell`).
- **Root privilege escalation:** A local privilege escalation vulnerability was discovered in the
  shipped diagnostic daemon `uds_doip_server` (listening on TCP 13400, running as `root`). The server
  stubs out authentication checks (`DoIP_IsAuthRequired() -> 0`) and uses a trivial two's complement
  seed-to-key algorithm, allowing arbitrary file writes as `root` (UID 0) via UDS RequestFileTransfer (SID 0x38).
  This exploit chain is documented in [PHASE2-SECURITY-SURFACE.md](PHASE2-SECURITY-SURFACE.md) and
  [FIELD-GUIDE.md](FIELD-GUIDE.md), with PoC implementation in [`scripts/doip_root_exploit.py`](../scripts/doip_root_exploit.py).

## Things that are genuinely dangerous

- **Firmware / OTA UI.** `com.desaysv.ivi.vds.upgrade/.view.activity.UpdateActivity` and the eng
  menu's Burn/OfflineConfig/AllUpdate screens can flash the unit. Do not trigger them casually —
  a bad flash bricks the head unit.
- **Root file writes via DoIP (`uds_doip_server`).** The DoIP privilege escalation exploit writes
  arbitrary files as UID 0 across writable partitions (`/data/misc/adb/adb_keys`, `/data/system/packages.xml`,
  scripts executed by root init triggers). Corrupting critical system databases or permissions can cause
  a persistent bootloop requiring EDL recovery.
- **Body‑control actuators.** The catalog in [05](05-command-catalog.md) includes door unlock,
  trunk/tailgate, windows and remote‑start IDs. Even though writes are no‑ops on this unit today,
  treat that list as live: read and confirm state before any write, never auto‑fire, and gate
  anything like it behind an explicit user action.
- **The MCU socket `192.168.8.1:10004`.** Sending malformed/unwrapped frames to it desynced the
  bus in one test and required a **car power‑cycle** to recover. Don't fuzz it with the car in a
  state where that matters.
- **Eng‑mode config screens** change factory/vehicle configuration (Board/OfflineConfig/StrMode/
  demo). Open and read; don't toggle.

## Redactions

Per the author's choice, **personal identifiers are redacted** and replaced with placeholders:

- the vehicle **VIN** and head‑unit **serial** → `<VIN>` / `<SERIAL>`
- the car's **Wi‑Fi‑hotspot SSID/password** and SoftAP SSID suffix
- the saved **`adbauth.key`** (the file itself is not in this repo; the method is)
- T‑Box **serial / ICCID / IMSI** (shown truncated)

**OEM / fleet‑wide constants are left in** (the engineering‑mode AES‑CMAC key, the FtpServer AES
key and its hardcoded `desaysv`/`avm` accounts). These are recoverable by anyone from the shipped
APKs and are already published in public client‑side web calculators; redacting them would only
obscure how the mechanism works.

## License / use

Documentation, licensed **[CC BY 4.0](../LICENSE)** — reuse it with attribution. Use it on
vehicles you own or are authorized to work on. You are responsible for complying with your local
laws and your vehicle's warranty/terms.
