# 06 — Ambient lighting

Ambient light was the first feature targeted and the most thoroughly tested — and it's a clean
case study in *"the protocol decoded cleanly but an unprivileged app still can't drive it on this
unit."* Three control paths exist; here's what each is and whether it works.

## The MCU protocol (decoded from the official app)

Captured by `logcat`‑watching the **official** vehicle‑settings app
(`com.desaysv.vehiclesetting`) change the ambient color, which logs the exact MCU bytes via
`Spi2Ethenet Write:` and `SVPVehicleEmulator doSetValueFromClient() prop:0x3d01`.

- **Ambient light = vehicle‑bus prop `0x3D01`.** MCU frame: `05 <prop_hi> <prop_lo> <len>
  <payload…>`, where `05` is the message type. For `0x3D01` the payload is 8 bytes:

  ```
  05 3D 01 08 00 16 <COLOR> <B2> 1A 0A 16 01
  ```

  - `<COLOR>` is a **continuous** color value (color‑wheel), *not* an 8‑preset index: red ≈ `0x09`,
    blue ≈ `0x01–0x03`, green ≈ `0x0F–0x11`.
  - `<B2>` varies (`0x33/0x35/0x11/0x20`) — phase/brightness, not fully decoded.
  - Sub‑op byte is `0x16` on the live car (an earlier capture showed `0x17`; firmware/path differ).
  - The official app sends it as a **2‑frame pair**.

This is the exact wire packet format: opcode `0x3D01` with `payload8` → the `{{…}}` frame documented in
[03](03-mcu-wire-protocol.md).

## Path 1 — VDBus CAR_SETTING (the "correct" API)

On this Tiggo 4, ambient lives on the carinfo **CAR_SETTING `0x50001`** module:
`0x07` on/off, `0x0C` brightness, `0xC0` RGB color (`int[]{0,R,G,B}`), `0x76` light‑show, `0x08`
music‑rhythm, `0x29` welcome. The official writer is `SVVehicleSetting`
(`com.desaysv.ivi.extra.project.carinfo.AmbientLightID`).

**Verdict: reads work, writes don't.** `sendItemValue(0x50001, …)` / `sendItemValues(0x50001,
0xC0, {0,R,G,B})` are silently dropped from an untrusted app ([04](04-vdbus-untrusted-access.md)).
This is the general VDBus‑write no‑op, not an ambient‑specific quirk.

## Path 2 — the MCU socket `192.168.8.1:10004`

Direct socket route. The socket **connects** from an unprivileged app and the `0x3D01` frame is
byte‑perfect (verified with both a Java `Socket` and `/proc/net/tcp` state `01` from shell).

**Verdict: no effect on this unit.** Sending the byte‑perfect `0x3D01` frame — single and bursted
~40×/3 s — produced **zero** light change. Likely cause: the platform continuously re‑pushes
ambient state to the MCU from the VDBus CAR_SETTING store (which we can't write), overwriting any
one‑shot socket frame; or the MCU only honors ambient from the SPI bridge, not the socket.
**Raw/unwrapped frames are dangerous** — they desynced the MCU stream and required a car
power‑cycle. (An early "lights went off" was actually the *headlights*; ambient is gated on
headlights being on.)

## Path 3 — a vendor ContentProvider (for *other* units)

Some variants ship `com.simba.atmospherelamp` (SimbaAmbientLight), which pokes its **own exported,
permission‑less** ContentProvider and drives the hardware itself — no socket, no VDBus, no root:

```java
ContentResolver.update(Uri.parse("content://com.simba.AppContentProvider/lamp"), cv, "", null); // returns 1 on success
```

`cv` key `"ACTION"` ∈ `ACTION_OPEN` | `ACTION_CLOSE` | `ACTION_SEND_COLOR` (+ int
`"SEND_COLOR_INDEX"`) | `ACTION_RANDOM_COLOR` | `ACTION_MULTI_COLOR_MODEL` | `ACTION_MUSIC` |
`ACTION_SPEED_MODEL` | `ACTION_DRIVE_MODEL` | `ACTION_MODEL_COLORSEEK` | `ACTION_RESET` |
`ACTION_PART_CONTROL` (front/back, on/off) | `IS_OPEN` (status probe). Brightness:
`ACTION_TEMPORGRY_UPDATE` + `ACTION_TEMPORGRY_COLORINDEX` + `ACTION_TEMPORGRY_BRIGHTNESS`.

**Verdict on this Tiggo 4: not applicable.** `com.simba.atmospherelamp` is **not installed** here
(every provider call returned r = −1). It's kept documented because other DeSay/Chery units ship it
and there it's the easy path.

## Bottom line

> Ambient light is **not controllable from an unprivileged app on this unit.** All three paths
> were walked: VDBus writes are dropped, the MCU socket is ignored, and the easy ContentProvider
> app isn't present. The decode work (`0x3D01`, the frame format, the IDs) is complete and correct
> and is kept here for any future attempt from a privileged context.
