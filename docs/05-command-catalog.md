# 05 — Command & signal catalog

The full, extracted catalog lives next to this file:
**[`COMMAND-CATALOG.md`](COMMAND-CATALOG.md)** — ~2,000 numeric command IDs across 206 sections,
pulled in cleartext from `SVEngMode.apk` smali (369 CarInfo IDs + 1,949 VD‑bus event IDs +
CabinLan message/event/requester/result enums). Every entry is `NAME = 0xID`.

A second, signal‑oriented cut (593 signals across 19 modules) was RE'd from the DeSay CarInfo ID
classes in `SVVDSCarLan` / `SVEngMode` — same `NAME = 0xID` form, grouped per module.

## How addressing works

Every property is addressed by **`module`** and **`id`**. In the VDBus AIDL you pass them
separately (`getItemValue(module, id)`); in the native vehicle‑bus layer they're packed as
`module << 16 | id`. **The same `id` means different things in different modules** — always carry
the module.

## Module map

| Module | Value | Module | Value |
|---|---|---|---|
| CAR_SETTING | `0x50001` | HVAC | `0x5000a` |
| NEW_ENERGY | `0x50002` | RVC | `0x5000b` |
| HUD | `0x50003` | AUTO_PARKING | `0x5000c` |
| READONLY_INFO | `0x50004` | DIAGNOSIS | `0x5000d` |
| EXT_AMP | `0x50005` | OTA | `0x5000e` |
| CLUSTER | `0x50006` | ACCOUNT | `0x5000f` |
| SELF_DIAG | `0x50007` | VR | `0x50010` |
| AVM | `0x50008` | METER_MALFUNCTION | `0x50011` |
| RADAR | `0x50009` | QUERY | `0x50012` |
| | | AMBIENT_LIGHT | `0x50013` |
| | | DVR | `0x50014` |
| | | CAR_COMPUTER | `0x50015` |
| | | AUTONOMOUS | `0x50016` |

## Frequently used IDs (and the on‑car reality)

Calibrated against a parked/engine‑off snapshot, so many are **tentative** — treat the catalog as
a map of *names*, and confirm the live decode on your own car.

| What | Module | ID | Notes |
|---|---|---|---|
| SoC | NEW_ENERGY `0x50002` | `0x0` (`ID_SOC`), `0x2c` (DISPLAY_SOC) | read ~21–22 % live |
| Odometer | NEW_ENERGY `0x50002` | `0x2e` (`ID_TOTAL_MILEAGE`) | multi‑byte → `getItemValues` |
| Range | NEW_ENERGY `0x50002` | `0x3f`, `0x36` (EV), `0x32` (HEV) | |
| Drive mode | NEW_ENERGY `0x50002` | `0x4` | 2 = Sport (confirmed), 0/1 = Eco/Normal (tentative) |
| Fuel % | CAR_SETTING `0x50001` | `0xc1` held 52 live — **but** `0xc1` is also catalogued `ID_CAR_BODY_PATTERN`; ReadOnly `0x44` reads wrong. Fuel decode is **unresolved** |
| Doors | READONLY_INFO `0x50004` | `0xf/0x10/0x11/0x12` | 0 = Closed, 1 = Open |
| Gearbox | READONLY_INFO `0x50004` | `0x1a` | 1=P (confirmed parked), 2=R, 3=N, 4=D (tentative) |
| Car speed | READONLY_INFO `0x50004` | `0x0` | read flat 0 even moving — **unproven**, likely needs `getItemValues` or another prop |
| VIN / SN | — | — | `getItemValues` returned nothing → read `sys.vehicle.hardware.vin.code` |

### Ambient‑light IDs (CAR_SETTING `0x50001`)

`0x07` `ID_CAR_AMBIENT_LIGHT` (on/off), `0x0C` brightness (level, not %), `0xC0`
`ID_CAR_RGB_COLOR` (int[]{byte0,R,G,B}), `0x76` light‑show, `0x08` music‑rhythm, `0x29`
welcome‑light. See [06](06-ambient-light.md) — note these are the stored‑state props; writing them
does not move the lights on this unit.

### HVAC (SVVDSCarInfo `AC_*`)

`AC_DRIVER_SET_TEMPERATURE`, `AC_BLOW_SPEED_LEVEL`, `AC_CIRCULATION_MODE`, `AC_AUTO_DEFROST`,
`AC_DUAL_MODE`, `AC_FRAGRANCE` (+ consistence level), seat heat/vent. Example HvacID values:
`ID_AC_BLOWER = 0x43`, `ID_AC_CIRCULATION_MODE = 0x8`, `ID_AC_DUAL_MODE = 0x3`.

### Actuators (real body actions — present in the catalog, write path is a no‑op here)

`ID_TRUNK_OPEN_SWT`, `ID_REMOTE_START_SIGNAL`, `ID_CAR_SMART_LOCK_UNLOCK`,
`FUNCTION_REAR_DOOR_UNLOCK`, `ID_CAR_DRIVE_DOOR_UNLOCK_ONLY`, electric tailgate (open/close/height),
`FrontRow/BackRow_Chair_Window_Open/Close`. See the safety note in [00](00-safety-and-legal.md).
