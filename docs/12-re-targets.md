# 12 — RE targets (what else is reachable to build on)

Reachability‑probed candidate capabilities, ranked. Foundation: the untrusted VDBus read path
([04](04-vdbus-untrusted-access.md)) + the command catalog ([05](05-command-catalog.md)).

> **Reminder:** VDBus **writes do not actuate this vehicle** ([04](04-vdbus-untrusted-access.md)).
> Everything below that would "set" something is read‑only in practice; the catalogue is useful for
> *reading* state and as scaffolding for a future privileged path.

## 1. DMS / driver‑monitoring — ★ best (unguarded, direct bind)

iFlytek driver‑monitoring service — the richest *new* data, lowest risk (read‑only observation of
the owner's own cabin).

- **App:** `com.iflytek.autofly.icvpservice.pro` (system uid).
- **Entry:** exported, **no‑permission** service `MainService`, action **`com.iflytek.autofly.ICVP`**
  → any sideloaded app can `bindService` it. The service holds `CAMERA` + `RECORD_AUDIO`.
- **AIDL surface (register listeners):**
  - `DmsDataAidl` / `DmsDataListener` — drowsiness, distraction, fatigue.
  - `DmsAgeGender` — age/gender estimation.
  - `DmsGestureAidl` / `DmsGestureListener` — hand gestures.
  - `DmsRecogAidl` / `DmsRecogListener` — face recognition / driver identity.
  - **`DmsImageAidl` / `DmsImageListener` — the driver‑camera image stream.**
- **Fallback (if AIDL changes):** VDBus CarLan beans over untrusted access — `VDDmsBehavior`,
  `VDDmsFaceType`, `VDDmsHealthResult`, `VDDmsRecognitionStatus`.
- **Status:** blocked *remotely* only by tooling (needs the iFlytek ICVP SDK extracted; no dex2jar/
  jadx CLI on the analysis box) or an on‑car probe. The bind itself is open.

## 2. Vehicle body control — biggest surface, safety‑weighted, **writes are no‑ops here**

Mostly catalogued already (160 CarSetting IDs + full HVAC). The new layer would be actuators:

- **Settings:** ambient light, AC mode, seat heat/vent, auto‑lock, DRL, high‑beam, defrost,
  drive‑mode.
- **HVAC (`AC_*`):** set temperature, blow speed, circulation, auto‑defrost, dual, **fragrance** (+
  level), seat heat/vent.
- **Actuators:** `ID_TRUNK_OPEN_SWT`, `ID_REMOTE_START_SIGNAL`, `ID_CAR_SMART_LOCK_UNLOCK`,
  `FUNCTION_REAR_DOOR_UNLOCK`, `ID_CAR_DRIVE_DOOR_UNLOCK_ONLY`, electric tailgate, window open/close.
- **Path:** VDBus write/inject via untrusted access — **but proven a no‑op on this car.** A working
  actuation path would be the MCU socket / a privileged injector; neither worked from an
  unprivileged app.
- **⚠️ Safety:** these literally unlock doors, pop the trunk, roll windows, remote‑start. Read and
  confirm state before any write, never auto‑fire, gate behind explicit user action.

### Build status

A companion sideloaded app prototyped a **body‑control module**: a settable‑CarSetting catalogue
(SETTING vs ACTUATOR tiers), an actuator gate (arm → one‑shot token → confirm → auto‑disarm, with
settings passing freely), and a write wrapper with a read‑before‑write audit. Unit tests pass, the
APK builds, and actuators are API‑only + gated (not wired to any button). On‑car, the writes are
**expected no‑ops** — the gate and catalogue remain useful scaffolding.

## 3. Cluster / HUD — constrained

`DISPLAY_TO_CLUSTER` / `CLUSTER_*` / `NAVIGATION_DISPLAY_TO_CLUSTER` / `AR_HUD_*` events
(SVMeterInteraction + a shared cluster API used by CheryDVR, CarPlay, Android Auto, Assistant) are
reachable via untrusted access — but content is **predefined types** (nav / media / diag /
version), **not** arbitrary pixels. Medium payoff: you can push supported content, not a free
framebuffer.

## Recommendation

DMS first (clean unguarded bind, richest novel data, lowest risk), body control second (biggest
surface — but accept it's read‑only until a privileged actuation path exists), cluster third.

## Car‑only TODO (next visit)

- Probe the MCU HTTP service `http://192.168.8.1:60004`.
- Capture `/data/update` if an OTA ever stages (via the FTP path, [08](08-ftp-server.md)).
