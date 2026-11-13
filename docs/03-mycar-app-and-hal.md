# 03 — The MyCar app & the HAL socket

**MyCar** (`com.vadimbrk.mycar`, app label *"Driver Settings"*) is a third‑party IVI
customization app for these DeSay head units. It was analyzed because it is a working example of
an *untrusted* app that controls the car — so its mechanisms map the attack surface available
without root. Two things came out of it: a **native code packer**, and the **MCU wire protocol**.

## The packer (why the source isn't in the APK)

MyCar is a **natively packed** app — the real code is not in the APK's DEX.

- The manifest declares a MainActivity plus many services/activities, but the DEX contains only
  ~18 `com.vadimbrk.mycar` classes: the `R.*` tables and three shims (`HalCtl`, `DmsNative`,
  `MycarApplication`). `HalCtl`/`DmsNative` have **zero** static callers — their callers live in
  the hidden DEX.
- `MycarApplication.attachBaseContext()` / `onCreate()` are **native**; `<clinit>` loads
  `loader`, `dmsctl`, `halctl`. `libloader.so` decrypts the real DEX in memory at launch and
  installs it into the classloader.
- The encrypted payload hides in **non‑standard PNG chunks**: `res/K7.png` and `res/Ly.png` each
  carry a `tpZj` chunk (1,603,700 bytes, byte‑identical); `res/WP.png` carries a `baUn` chunk
  (364,084 bytes). All ~7.999 entropy → encrypted.
- Crypto (Ghidra): `libloader.so` statically links BoringSSL and uses **AES‑256‑GCM** (32‑byte
  key, 12‑byte IV, tag‑verified). The key is **not** a `.rodata` constant — it's assembled at
  runtime into a context struct, and the decrypt routine is reached only through
  computed/indirect (MBA‑obfuscated) calls.

### Anti‑tamper & why offline recovery failed

`libloader.so` is control‑flow / mixed‑boolean‑arithmetic obfuscated:

- `attachBaseContext` (native) throws `SecurityException: Protected runtime unavailable` when it
  detects emulator/root (probes `/system/xbin/su`, `ro.kernel.qemu=1`, `ro.hardware=ranchu`,
  `ro.build.tags=dev-keys`, `ro.debuggable=1`).
- `JNI_OnLoad` returns `JNI_ERR` when Frida is present (probes `/proc/<pid>/maps`, a localhost
  Frida port, `su`).
- The root/emu/Frida checks are **fused** into one ~76 KB, non‑contiguous, MBA‑obfuscated
  mega‑function welded to the decryption orchestration — there is no isolated `isRooted()` to
  patch.

Verdict: **static AES‑key recovery is blocked** (runtime‑assembled key + GCM auth); the emulator +
Frida path is blocked (direct‑syscall + in‑process Frida detection); a no‑Frida native patch is
intractable (the fused 76 KB function). The only realistic recovery is `frida-dexdump` **on the
real, trusted head unit** — which needs root, which this unit does not have. So MyCar's precise
command numbering stays unrecovered. It isn't needed for modding: the full DeSay control
vocabulary is available in cleartext from the stock apps ([05](05-command-catalog.md)).

## The MCU wire protocol (`192.168.8.1:10004`)

All of MyCar's vehicle control funnels through `HalCtl.send(int cmd, int... payload)`, whose
native sibling is `halctl_send(cmd, buf, len)` in `lib/*/libhalctl.so` (arm64, NDK r26d, stripped),
fully reversed in Ghidra:

- **Transport:** TCP to **`192.168.8.1:10004`**.
  `socket(AF_INET, SOCK_STREAM | NONBLOCK | CLOEXEC)` → `connect` → **one** `sendto(…, MSG_NOSIGNAL)`.
  ~1 s total deadline via `poll`. No `recv`/`read` — the socket is **write‑only**, a control path,
  not a telemetry channel.
- **Frame:**

  ```
  7B 7B | (cmd>>8)&7 | (cmd>>8)&0xFF | cmd&0xFF | (len+1) | 00 | payload[len] | 7D 7D
  └─{{─┘   └────────── header ──────────────────────────┘                     └─}}─┘
  ```

  i.e. `{{` (0x7b7b) delimiters wrapping a header + payload (≤250 bytes), closed by `}}` (0x7d7d).
  For the ambient‑light command `0x3D01` with payload `16 09 35 1A 0A 16 01` (a "red" sample) the
  bytes on the wire are:

  ```
  7B 7B 05 3D 01 08 00 16 09 35 1A 0A 16 01 7D 7D
  ```

- **Return codes:** `0`=SENT, `-1`=INVALID_ARGUMENT, `-2`=PAYLOAD_TOO_LARGE, `-3`=SOCKET_ERROR,
  `-4`=CONNECT_FAILED, `-5`=TIMEOUT, `-6`=SEND_FAILED, `-7`=JNI_ERROR.

This frame format matches the MCU's own `05 <prop16> <len> <payload>` SPI‑to‑Ethernet protocol
(see [06](06-ambient-light.md)). **`DmsNative.dmsState(boolean)`** is a separate toggle into
`libdmsctl.so`.

> This `192.168.8.1:10004` path is **separate** from the DeSay "CabinLan" bridge
> (`192.168.225.1:16000`, SOME/IP via `com.desaysv.libsomeiputil`). Different IP+port = parallel
> paths; MyCar does not ride CabinLan.

### Does it work from an unprivileged app?

The TCP connection **establishes** from an ordinary app and the frame is byte‑perfect — but on
this unit, sending even byte‑perfect frames produced **no vehicle effect** (ambient tested
exhaustively, see [06](06-ambient-light.md)). The socket reaches the MCU; the MCU doesn't accept
one‑shot frames from us the way it accepts the platform's persistent connection. **Never send
raw/unwrapped frames to `:10004`** — doing so desynced the bus and needed a car power‑cycle.

### Distribution (observed)

MyCar updates from a public‑read S3 bucket `j7apks` (us‑east‑2):
`https://j7apks.s3.us-east-2.amazonaws.com/alpha.apk` / `beta.apk`. Objects are public by exact
name; `ListBucket` is denied, so the bucket can't be enumerated. The app ignores the global HTTP
proxy and uses its own HTTP stack.
