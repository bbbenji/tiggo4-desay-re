# 02 — ADB access

## The problem

On this unit ADB is enabled through the engineering menu and authorized by a signed
**`adbauth.key`** placed at the USB root. The key is a 256‑byte RSA‑2048 signature over
`(deviceID + date)` that the unit verifies against its own system clock. The factory/dealer keys
are **date‑bound** and expire — once expired, enabling ADB just shows *"contact dealer"*.

## The method that works (clock rollback)

The unit validates the key's signature against the **current system clock**, so an *old* key that
has "expired" revalidates if you set the clock back to the date the key was issued for.

1. Place at the USB stick root:
   - the saved `adbauth.key` *(the key file is personal to the vehicle and is **not** in this repo)*
   - `svengmode.flag` — contents: `1`
   - `svtasklist.flag` — contents: JSON `{"action":"eu_debugmode"}`
2. **Roll the unit's clock back** to the key's issue date (disable auto‑time first).
3. Run the first‑install USB engineering‑menu method → ADB is enabled.

Engineering‑menu passwords for these units are also produced by public calculators
(drive‑smart.app/calculators, jcartools CheryCoding); the `adbauth.key` *generator* is token‑gated
through jcartools. The password mechanism is fully documented in [07](07-engineering-mode.md).

### Caveats observed on‑car

- The ADB link is **flaky**: it drops within seconds unless the clock rollback is **freshly
  applied right before connecting** (the auth window is tied to the rolled‑back date).
- Restore auto‑time afterward — but note that re‑enabling network time will re‑expire the key, so
  re‑roll before the next session.

## What ADB gives you — and what it doesn't

ADB comes up as **shell (uid 2000, in the `log` group), not root.** `adb root` is refused
(production build). So you **can**:

- read system‑wide `logcat` (this is how a lot of the protocol decoding here was done),
- drive the UI (`am start`, `input`, `uiautomator dump` — `screencap` is blocked on `user`
  builds: *"user version is not allowed"*),
- set a global HTTP proxy, reach `192.168.8.1:10004` and the internal net,
- `pm install` / `pm list` / `appops` / `getprop`.

You **cannot (via standard ADB alone)**:

- `adb root` directly — production build (`ro.secure=1`, `ro.debuggable=0`) refuses `adb root`.
  Getting UID 0 requires privilege escalation via the DoIP diagnostic exploit (see
  [`scripts/doip_root_exploit.py`](../scripts/doip_root_exploit.py) and [FIELD-GUIDE.md](FIELD-GUIDE.md)),
  after which root file manipulation and full UID 0 execution become possible.
- Directly read `/data/data/*` of other private apps or touch `/vendor/lib64` without escalating to root first.
- Start non‑exported engineering sub‑activities to bypass the password gate — tried, it throws
  `SecurityException: not exported from uid 1000` ([11](11-hidden-functionality.md)).
