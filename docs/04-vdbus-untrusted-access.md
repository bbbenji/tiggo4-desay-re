# 04 — VDBus untrusted access (the central unlock)

This is the finding everything else reads from: **an ordinary sideloaded app can read the entire
DeSay vehicle‑data bus (VDBus) with no root and no platform signature.** Writes, however, **do not
actuate this vehicle** — see the box at the bottom.

## The recipe

**1. Declare one `normal` permission.**

```xml
<uses-permission android:name="ivi.vds.permission.SERVICE"/>
```

This permission is *defined by* `SVVDSUpgrade` with `android:protectionLevel="normal"` (verified:
aapt `type 0x11)0x0`). `normal` → **auto‑granted at install**, no signature match, no prompt.
Any sideloaded app (`untrusted_app`) holding this single permission can access the bus; so does TurboDog, a
`/product/app` performance meter.

**2. Bind the VDS module service with an explicit Intent** (package + action). These services are
`exported=true`:

| Module | Package | Action |
|---|---|---|
| CarInfo (telemetry + settings) | `com.desaysv.ivi.vds.carinfo` | `action.desaysv.ivi.vds.carinfo.SERVICE` |
| CarState | `com.desaysv.ivi.vds.carstate` | `action.desaysv.ivi.vds.carstate.SERVICE` |
| Setting / HVAC / … | `com.desaysv.ivi.vds.<module>` | `action.desaysv.ivi.vds.<module>.SERVICE` |

Use `Context.bindService(Intent().setPackage(pkg).setAction(action), …)`. **Not**
`ServiceManager.getService` — that native binder (`com_desaysv_ivi_vds`) is SELinux‑`find`‑walled
to platform/system/HAL domains, and the `avc denied find` lines in logcat are DeSay‑side noise,
not your app.

**3. Talk AIDL.**

- `getItemValue(module, id)` → read an int (returns a **byte**, 0–255, on this bus).
- `getItemValues(module, id)` → read an int[] (for multi‑byte values).
- `getStringValue(module, id)` → read a string (VIN/SN).
- `sendItemValue(module, id, value)` / `sendItemValues(module, id, int[])` → write.
- `onVDBusNotify(...)` → a push callback.

The bundled client SDK is `com.desaysv.ivi.vdb.client.*` (e.g. a pruned `desay-vdb.jar` extracted
from `SVEngMode` via dex2jar). Its `VDConnector` builds the Intent + `bindService` for you.

## What reads cleanly (confirmed live)

VDBus bind + `getItemValue` **polling** works live: car speed, fuel %, SoC/battery, endurance/
range, consumption, water temp, GPS fix, drive mode, doors, gear, handbrake, TPMS, steering,
temperatures, VIN/SN. Practical notes learned on‑car:

- **The `onVDBusNotify` subscription does not reliably deliver** on this unit (doors/handbrake/TPMS
  stayed empty). **Poll** those props with `getItemValue` (on‑change, to limit volume).
- **VIN via `getItemValues` returned nothing** → read the system property
  `sys.vehicle.hardware.vin.code` instead (reflection; this is what eng‑mode does).
- **`255` (0xFF)** appears to be an "unavailable/invalid" sentinel for some props when the engine
  is off / stationary (instant consumption, avg power).
- Values are scattered across modules and **not always where the catalog implies** — the same id
  means different things in different modules (NewEnergy `0x0` = SoC, not speed). See
  [05](05-command-catalog.md).
- Multi‑byte values (odometer ~2197, range ~517) are **not** single bytes via `getItemValue` —
  use `getItemValues` and sweep ids past `0xC8`.

## Telemetry surface (read path)

- **Tier A — any app, zero special perms:** fused GPS/trips (ACCESS_FINE_LOCATION); ~33
  `dsv.intent.action.carsignal.*` broadcasts (POWER_NORMAL/OFF, DISPLAY_ON/OFF, HEADLAMP_ON/OFF,
  IN_REVERSE, DARKLIGHT/DAYTIME, BT/Wi‑Fi/audio state); device sensors.
- **Tier B — `ivi.vds.permission.SERVICE` (normal):** full CarInfo/CarState read — speed, RPM,
  engine/gearbox state, handbrake, brake pedal, fuel %, SoC, battery, odometer (GRAND_TOTAL_KM),
  range (ENDURANCE_KM), trips, all doors/hood/trunk, TPMS, steering angle, water temp, oil, VIN,
  SN, power mode, drive mode, diagnostics.
- **Tier C — Neusoft T‑Box ContentProvider** `content://tbox.automotive.neusoft.com/` — the car's
  *own* GNSS (has a fix parked/ACC, unlike the HU GPS) plus VIN/IMEI/ignition/signal. See
  [10](10-tbox-telematics.md).

> ### ⚠️ Writes do **not** actuate this vehicle (proven on‑car)
> `CarInfoProxy.sendItemValue(...)` / `sendItemValues(...)` to CAR_SETTING are **silently
> dropped** — write‑then‑readback shows "✗ ignored", nothing moves. This was confirmed on‑car:
> an ambient‑light writer built against this path had no effect and was dropped. The carinfo CAR_SETTING props
> (`0x07`, `0xC0`, …) are just stored UI state; the real hardware command is `0x3D01` on the MCU
> socket ([06](06-ambient-light.md)) — and raw replay there didn't take either. So **any
> "body‑control / set" feature over the VDBus is a no‑op on this car**; keep the catalog for
> *reading* state, drop the expectation that writes fire. Actuation needs system privileges (the
> platform's persistent MCU connection), which an untrusted app doesn't have.

## Bonus: T‑Box ContentProvider contract (RE'd from TurboDog/QiblaCompass consumers)

Authority `content://tbox.automotive.neusoft.com`. Request/response model:

- **Request:** `.../req?cmd=<X>Request`, X ∈ GPS, VIN, BD, GL, Log (also IMEIInfo, KL15State,
  CellInfo, ICMUnits, ECUVersion, WorkModel, MicState).
- **Read/observe:** `.../<X>Response` and `.../<X>Notification` (e.g. `/GPSResponse`,
  `/GPSNotification`, `/VINResponse`, `/KL15StateResponse`, `/CellInfoResponse`).
- **GPS columns:** `latitude longitude altitude speed bearing accuracy UTC`. **Cell:** `lac mcc
  mnc` (+cellid) `iccId`. Scalars use generic `VALUE`/`STATUS`/`result` columns.

The host app `com.desaysv.tboxinfo` is UI‑only; the authority is served by the Neusoft TSP client.
Verify it resolves on‑device before relying on it.
