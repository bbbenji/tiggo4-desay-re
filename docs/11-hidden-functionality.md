# 11 — Hidden functionality map

A suite‑wide REA sweep of the dumped app set for hidden menus / exported components / access
surfaces. Eng‑mode is in [07](07-engineering-mode.md); the FTP server in [08](08-ftp-server.md);
VDBus binding in [04](04-vdbus-untrusted-access.md).

## SVEngMode (`com.desaysv.engmode`)

Full hidden menu; only `MainActivity` is exported (AES‑CMAC‑gated). Sub‑screens are non‑exported
`com.desaysv.engmode.ui.*`: Dsp, Adb, Burn, OfflineConfig, TboxInfo, TBOXUpdate/AllUpdate/VR/
GpsUpdate, DTC, SelfDiag, Board, Ethernet, Temperature, BatteryInfo, LimitTest, Camera, Speaker/
Screen/Char tests, Exhibition (demo), StrMode, FileBrowse, Sources, Tuner, Mic, Rvc, CarPlay, IPod.
Powerful perms: `MASTER_CLEAR`, `REBOOT`, `WRITE_SECURE_SETTINGS`, `CAR_VENDOR_EXTENSION`,
`READ_FRAME_BUFFER`.

- Embeds a **"fairy" dynamic DEX‑plugin loader** (`com.limpoxe.fairy`, `PluginStub*` hosts) +
  exported `UsbBroadcast` receiver on `MEDIA_MOUNTED` → can load external eng plugins from USB.
- **Password‑bypass via `am start` of a sub‑activity: does NOT work** (live‑proven) —
  `SecurityException: not exported from uid 1000`.

## SVDVPTestMediaApp (`com.desaysv.mediadvp`) — unguarded, no password

Factory test suite, launchable directly (exported, no permission):

- Activities `com.desaysv.engmodule.ui.{TunerActivity, DabActivity, USBTestActivity,
  VideoPlayTestActivity, AndroidTunerActivity}` + `.ui.MainActivity`.
- Exported services `EngModeService` (action `com.desaysv.engmodule.eng`; int extra `TYPE` 0=radio
  1=usb 2=dab 3=video 999=dvp → launches the matching test) and **`OutSideControlService`** (action
  `com.desaysv.mediadvp.action.startSource`; extras `source` ∈ USB0..4_music/USB0..3_video/AM/FM/
  DAB → opens that media source).

Net: unguarded external media/test control, **not** a privilege lever.

## FtpServer

See [08](08-ftp-server.md). Apache FtpServer on `192.168.8.2:2121`, hardcoded `desaysv`/`avm`
accounts, config AES‑encrypted with a recoverable key.

## EngineeringService (native, root‑gated — not an app)

`com.desaysv.ivi.vds.engineering.service.EngineeringService` is only a VDS *constant*; the
implementation is the vendor binary `vendor-bin/com.desaysv.ivi.engineering@1.0-service`, declared
`user root / group root`, seclabel `desaysv_engineering`, **disabled** (on‑demand). It's a thin
HIDL wrapper over the vehicle‑bus HAL (`libdesaysv_vehiclebus.so`, `libdesaysv_vehicledevice.so`) —
a **root HIDL gateway** to get/set/**inject** vehicle props, same surface as the `desaysv_vdb_*`
tools. No secret command language; value is in decompiling `libdesaysv_vehiclebus.so` to extend the
protocol map. On the live unit it is **not** a running binder/HIDL service (`service list` /
`lshal` show only idenauth / vehicledevice / vehiclelan.proxy) — Ghidra‑only.

## Other exported components (guard‑checked)

- **`SecurityLogProvider`** (`com.desaysv.ivi.vds.securitylog`) — exported + unguarded, but
  `query()` just `return null`. It's a write‑sink (apps `insert` security events), **not** a
  readable data surface. (An exported `insert` could let an app forge log entries — minor.)
- **PlatformAdapter** (`com.desaysv.ivi.adapterservice`, system uid) — 3 exported no‑permission
  AIDL services, but they're **audio** adapters, not vehicle control: `Hvacdvc` = HVAC *Dynamic
  Volume Control* (raises media volume vs. fan noise), `Sdvc` = Speed‑Dependent Volume Control,
  `Visualize` = audio visualizer. Binding them only tweaks volume gain.
- Exported but guard‑unverified: `com.desaysv.ivi.vds.upgrade/.service.UpgradeService`,
  `com.desaysv.setting/.SettingService`, `com.desaysv.vehiclesetting/.CommonService`,
  `com.desaysv.assistant/.AssistantService`, launcher `LauncherService`/`SpriteService`/
  `ScreensaverService`, `com.desaysv.meterinteraction/.service.MeterService`.
- `com.desaysv.ivi.vds.upgrade/.view.activity.UpdateActivity` is **exported** — launching it leads
  to flashing. **Do not trigger.**

## Launcher‑referenced apps not installed on this unit

(No binaries — other‑variant or removed; pull their APKs from a market variant if you want them.)
`com.desaysv.svcardiagnose` (vehicle diag), `com.desaysv.trackmode`, `com.desaysv.tboxcall`
(RoadRescue), `com.desaysv.electric`, `com.desaysv.personalcenter`, `com.desaysv.svuploadlogapp`,
`com.desaysv.ipc`, `com.desaysv.sprite`.

## Live endpoints noticed in logcat

- `http://192.168.8.1:60004` — **MCU HTTP service** on the internal net (unexplored).
- `https://fotaapis-eu.cheryinternational.com:443` — FOTA backend; `http://ota_ivi` internal alias.

## `libdesaysv_vehiclebus.so` (Ghidra)

A C++ typed property‑bus client: `VehicleBus` singleton (`bulkGet` + get/set), `VehicleBusStubImpl
::inject(ID=%x)`, typed `VehicleBusBundle` (getInt/Boolean/Float/String/Byte/Long/Double + vectors),
Connector/Router/CallbackManager. Property IDs are numeric (`module<<16 | id`) — the same surface as
[`COMMAND-CATALOG.md`](COMMAND-CATALOG.md), no new string command set in the lib itself.
