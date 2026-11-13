# 07 — Engineering mode

The DeSay engineering menu (DSP, diagnostics, board info, Ethernet, T‑Box info, ADB enable, burn/
offline‑config, demo mode, …) is behind two things: a launcher gesture and a password. Both are
bypassable/derivable as an ordinary app.

## Launching the menu (no root)

`com.desaysv.engmode/.MainActivity` is **`exported=true` with no component permission** → any app
can `startActivity(ComponentName(...))` it directly, skipping the stock "tap version 8×" gesture.
That lands you on the full eng menu.

- The individual feature screens (`com.desaysv.engmode.ui.*`) are `exported=false` (targetSdk 31)
  and load through an internal plugin/stub framework (`PluginStubActivity*`) — they **cannot** be
  deep‑linked; navigate the menu to reach them.
- **Tried and failed:** starting a non‑exported `ui.*` sub‑activity from `adb shell` to skip the
  password → `SecurityException: Permission Denial … not exported from uid 1000`. The password
  gate on `MainActivity` cannot be skipped this way; shell (uid 2000) can't start uid‑1000
  components.
- Other hidden entry points: `com.desaysv.tboxinfo/.ui.activity.TBoxInfoMainActivity`,
  `com.desaysv.mediadvp` factory media tests ([11](11-hidden-functionality.md)). No DeSay dial
  `SECRET_CODE` exists.

## The password (AES‑CMAC, "Version 3.0")

The active check is in `MainActivity$d.a()` + `util.a` ("AESCMACUtils"). The password is the last
6 hex characters (uppercase) of an AES‑CMAC:

```
vin   = SystemProperties.get("sys.vehicle.hardware.vin.code")   # full 17‑char VIN
input = vin + str(ranNum) + "0123"      # ranNum = the 4‑digit number shown on screen (per session)
mac   = AES_CMAC(K, input.utf8)         # util.a.b()
pw    = hex(mac).upper()[-6:]           # util.a.c(): last 6 hex chars
```

(There are legacy, unused paths: V1 = `last6(802018 * productId)`, V2 =
`last6(566789 * productId * ranNum)`, and a `parseSnPassword` decimal method. Version 3.0 is the
one that validates on this unit.)

### The key `K`

`K` is a **16‑byte global constant** — the *same* key across the Chery/Jaecoo/Omoda fleet. It is
**not** shipped in `SVEngMode.apk`; the unit receives an encrypted key blob over VDB event
`0x7926 id 0x77` (vehicle‑provisioned) and `util.b.c()` AES‑CBC‑decrypts it into `originalAESKEY`.
But because the public web calculators (jcartools, drive‑smart, japp.cloud) must compute the same
password, the constant is recoverable from japp.cloud's **client‑side** calculator JS
(`/_astro/PasswordCalculator…js`):

```
K (hex)   = 31697060faa6b4d5a5ad1c4d9a3bd07a
K (bytes) = [49,105,112,96,250,166,180,213,165,173,28,77,154,59,208,122]
```

> This OEM constant is included per the vehicle owner's decision: it is already in public web
> calculators and derivable from the shipped app, so redacting it would hide the mechanism without
> protecting anything. See [00](00-safety-and-legal.md).

### Worked example (the web calculator's own published example)

```
AES-CMAC(K, "LVVDB21B7RD012345" + "4312" + "0123")
  = 3efc4c280c92507e9c8a16c087eaddcd
last6 (upper) = EADDCD        # matches the public web calc
```

On a real unit: open `MainActivity`, read `ranNum` off the screen (or `logcat | grep Emode`), then
compute `last6(AES-CMAC(K, VIN + ranNum + "0123"))`. The VIN, `ranNum` and resulting password are
per‑vehicle/per‑session; `ranNum` rotates every session.

### Deriving `K` on‑device (no hardcoded key, no server)

An app running on the unit with VDBus access can read event `0x7926/0x77` itself and AES‑CBC‑decrypt
it to recover `K` locally. On‑car, `logcat` also prints it while the eng screen is open:

```
adb logcat | grep -E 'originalAESKEY|ivAes|Emode'
# AJSDispatcher_custom: originalAESKEY = 31697060FAA6B4D5A5AD1C4D9A3BD07A
```

## Safety

These screens change factory/vehicle configuration (Burn / OfflineConfig / Board / ADB / demo /
StrMode). **Open and read; never auto‑toggle.** The AllUpdate/burn paths can flash the unit.
