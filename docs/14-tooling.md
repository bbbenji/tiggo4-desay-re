# 14 — Tooling

What was used to produce the rest of this repo.

## Reverse engineering

- **REA** (`rea-agents`) as an MCP server, wired to two providers:
  - **Ghidra 12.1.2** — native binaries (`libhalctl.so`, `libloader.so`, `libdesaysv_vehiclebus.so`,
    the SOME/IP client proxies).
  - **JADX headless 0.7.1** — Android APK classes/methods/xrefs.
- **apktool** — smali/resource decode (this is how the cleartext command catalog
  [`COMMAND-CATALOG.md`](COMMAND-CATALOG.md) was pulled from `SVEngMode.apk`).
- **dex2jar** — to carve a bundleable `desay-vdb.jar` (the VDBus client) and, in principle, the
  Skyworth DVR SDK. Note: full `SVEngMode` dex → jar crashes `d8` with an NPE on the obfuscated UI
  classes; a *pruned* vdb+carinfo+common+platform class set dexes clean.

## On‑device

- **ADB (shell, uid 2000)** — see [02](02-adb-access.md). Primary techniques: system‑wide
  `logcat` capture (how the ambient and eng‑mode protocols were decoded live), `am start` / `input`
  to drive UI, `pm` / `appops` / `getprop`, `dumpsys`, `uiautomator dump` (`screencap` is blocked on
  the `user` build).
- **frida / frida‑dexdump** — the intended MyCar‑unpack route; blocked on this unit for lack of
  root (and the packer detects Frida anyway, [03](03-mycar-app-and-hal.md)).

## Building sideloaded APKs (no Gradle)

The companion apps build with a plain `javac → d8 → aapt2 link → zipalign → apksigner` pipeline:

- **build‑tools 35.0.0 required** — bt34's `d8` (8.2.2) NPEs on JDK‑27‑compiled anonymous classes.
- platform `android-34`.
- debug‑signed; keep/back‑up the debug keystore (Android only accepts the same signer on upgrade).
