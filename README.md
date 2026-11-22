# Chery Tiggo 4 (2025) — DeSay SA8155 reverse‑engineering notes

Reverse‑engineering and modding notes for the **Chery Tiggo 4 (2025)** head unit — a
**DeSay SV** IVI built on **Qualcomm SA8155P**, running **Android 11**. This is a documentation
repository: it records how the head unit, its vehicle‑data bus, the companion phone app, the
engineering menu, the 360/DVR camera stack and the Neusoft T‑Box fit together, and what an
ordinary (non‑root, non‑platform‑signed) app can and cannot do on this platform.

All of the work here was done on a vehicle the author owns, for research, repair and
personal customization. Nothing here is an exploit against a remote service — it is local
analysis of hardware you have physical possession of.

> ⚠️ **Read [`docs/00-safety-and-legal.md`](docs/00-safety-and-legal.md) first.** Some of what
> is documented here changes factory/vehicle configuration, and some of it can actuate the car.
> Several findings were tested on the car and **did not work** — those negative results are
> recorded on purpose, because knowing what *doesn't* work is half the map.

---

## The one‑paragraph summary

The DeSay IVI exposes its whole vehicle‑data bus (VDBus) to ordinary apps through a service
whose permission is `protectionLevel="normal"` — so a sideloaded app can **read** essentially
every signal the car puts on the IVI bus (speed, RPM, fuel/SoC, odometer, range, doors, TPMS,
steering, temperatures, VIN, …). **Writes over that bus do not actuate this vehicle** — proven
on‑car. Actuation lives on an MCU socket (`192.168.8.1:10004`) that the stock apps drive over a
persistent connection, and raw replay to it did not take on this unit. The engineering menu is
launchable by any app and its password is a derivable AES‑CMAC; an internal FTP server with
hardcoded credentials sits on the car's internal Ethernet; the 360 camera is a SOME/IP video
service on a second SoC, not a local camera. The T‑Box can phone home but can't be turned into a
general internet uplink from software because its SIM is dead.

---

## Hardware at a glance

| | |
|---|---|
| Vehicle | Chery Tiggo 4, 2025 (platform **T19**), HEV and ICE variants seen |
| Head unit | **DeSay SV**, model `DesaySV` / device `msmnile_gvmq` / product `g7ph_t22_int` |
| SoC | Qualcomm **SA8155P** (msmnile_gvmq), arm64‑v8a |
| OS | Android 11 (SDK 30), build type `user` (production) |
| Internal net | IVI = `192.168.8.2`, peer SoC / MCU = `192.168.8.1`; second net `192.168.225.x` (T‑Box RNDIS) |
| Telematics | separate **Neusoft T‑Box** (own modem/SIM), SA8155 modem unused |
| Cameras | 8× V4L2 nodes `/dev/video51..58` (AVM/360), driven over Qualcomm QCarCam + SOME/IP |

---

## Documentation index

| # | Document | What it covers |
|---|----------|----------------|
| 00 | [Safety & legal](docs/00-safety-and-legal.md) | Scope, ownership, what's dangerous, redactions |
| 01 | [Hardware & platform](docs/01-hardware-platform.md) | SoC, partitions, internal networks, boot/security posture |
| 02 | [ADB access](docs/02-adb-access.md) | The clock‑rollback + saved‑key method to re‑enable ADB; root verdict |
| 03 | [MCU wire protocol & socket](docs/03-mcu-wire-protocol.md) | The direct `:10004` MCU wire protocol, frame format, and hardware bridge |
| 04 | [VDBus untrusted access](docs/04-vdbus-untrusted-access.md) | The central unlock: reading the vehicle bus from a normal app |
| 05 | [Command & signal catalog](docs/05-command-catalog.md) | Module map, signal IDs, how addressing works |
| 06 | [Ambient lighting](docs/06-ambient-light.md) | The `0x3D01` ambient protocol, three control paths, and why writes didn't take |
| 07 | [Engineering mode](docs/07-engineering-mode.md) | Launching the eng menu + the AES‑CMAC password derivation |
| 08 | [Internal FTP server](docs/08-ftp-server.md) | The `192.168.8.2:2121` FtpServer, its hardcoded creds and config decryption |
| 09 | [DVR / 360 cameras](docs/09-dvr-360-cameras.md) | Camera architecture and how a 3rd‑party app can (and can't) reach it |
| 10 | [T‑Box & telematics](docs/10-tbox-telematics.md) | Connectivity, the Chery TSP/PKI phone‑home map, and blocking it |
| 11 | [Hidden functionality map](docs/11-hidden-functionality.md) | Suite‑wide hidden/engineering/access surfaces |
| 12 | [RE targets](docs/12-re-targets.md) | DMS driver‑monitor, body control, cluster/HUD — reachability‑probed |
| 13 | [Vehicle 3D assets](docs/13-vehicle-3d-assets.md) | Interactive 3D car illusion: 91-frame 360° flipbook architecture on TextureView |
| 14 | [Tooling](docs/14-tooling.md) | REA/JADX/Ghidra setup, apktool, and build pipeline |

---

## Scripts

| Script | What it does |
|--------|-------------|
| [`scripts/chery_engmode_tool.py`](scripts/chery_engmode_tool.py) | **Engineering mode password & crypto toolkit.** Computes eng mode dialog passwords across cybersecurity modes (standard static multiplier, live random challenge, Version 3.0 AES-CMAC), decrypts/crafts `EngSecKey.txt` payloads using hardcoded AES keys, and validates date-scoped RSA `adbauth.key` signatures. |
| [`scripts/exploit_chain.sh`](scripts/exploit_chain.sh) | **Automated DoIP root exploit execution shell script.** Step-by-step implementation and documentation mapping raw packet stages to binary analysis findings. |
| [`scripts/adbdctl`](scripts/adbdctl) | **Vendor adbd control script.** Dumped vendor shell script demonstrating property toggling for `persist.sv.debug.adb_enable`. |
| [`scripts/doip_root_exploit.py`](scripts/doip_root_exploit.py) | **Root via UDS/DoIP arbitrary file write.** Connects to `uds_doip_server` (root, TCP 13400), which has authentication entirely stubbed out (`DoIP_IsAuthRequired()→0`, `DoIP_IsAuthenticated()→1`). Unlocks SecurityAccess level 0x35 — the seed→key algorithm is trivial two's complement (`key = -seed`). Then uses UDS RequestFileTransfer (SID 0x38) to write your ADB public key to `/data/misc/adb/adb_keys` as root, giving persistent ADB access. Requires ADB shell access (Phase 1) as a precondition. |
| [`scripts/full_extract.py`](scripts/full_extract.py) | **Full headunit data extraction** — four‑phase pipeline that combines shell‑level ADB pulls with DoIP root writes. **Phase 1:** pulls everything readable as shell UID — build props, init scripts, SELinux policy, all installed APKs, `dumpsys`/`logcat`/`dmesg` output, etc. **Phase 2:** uses DoIP to write an ADB pubkey, a root extraction shell script, and a proof file. **Phase 3:** attempts root execution of the extraction script (direct if adbd is root, otherwise via property‑trigger services). **Phase 4:** pulls root‑extracted data — keystore, WiFi/BT config, app databases (DeSay engmode/vehicle/systemui), persistent properties, vendor/CAN bus data, logs, SELinux policy, and partition layout. Use `--skip-doip` for shell‑only extraction or `--doip-only` for just the root writes. |

---

## Status & honesty notes

This is a working log, not a vendor manual. Where a value is marked *tentative*, *unconfirmed*
or *verify on car*, it has not been pinned down. Several headline ideas (VDBus writes, raw
ambient‑socket replay, internet‑via‑T‑Box, OTA version‑spoofing) are documented here as
**confirmed dead ends**, with the evidence, so nobody re‑walks them.

Personal identifiers (the author's VIN/serial, the car's Wi‑Fi‑hotspot password, the saved
`adbauth.key`) are redacted. OEM/fleet‑wide constants that are already recoverable from the
shipped APKs and from public web calculators are left in, because redacting them would hide how
the mechanism works without protecting anything.

## License

[CC BY 4.0](LICENSE) — reuse with attribution. See [`docs/00-safety-and-legal.md`](docs/00-safety-and-legal.md).
