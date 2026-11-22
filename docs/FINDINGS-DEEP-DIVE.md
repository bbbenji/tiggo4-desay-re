# Deep-Dive Findings — Extended Attack Surface Analysis

## 1. VDB Event Injection via Shell (`desaysv_vdb_emulator`)

**Binary**: `/vendor/bin/desaysv_vdb_emulator`
**SELinux label**: `u:object_r:cabin_all_exec:s0`
**Shell access**: CONFIRMED — `vendor_sepolicy.cil` grants:
```
(allow shell_30_0 cabin_all_exec (file (read getattr map execute entrypoint)))
(allow shell_30_0 cabin_all_exec (file (open execute_no_trans)))
```

**Usage**: `desaysv_vdb_emulator [set|get|inject] [EventID] [key] [type] [value]`

The `inject` command publishes arbitrary VDB events on the vehicle bus. From `adb shell`:
```
desaysv_vdb_emulator inject 0x8255 IntVector int 1
```
This injects `SV_ENG_SWITCH_ADBMODE` with `IntVector[0]=1`, targeting the EngMode plugin's
`AJSDispatcher.onVDEventVehicleHal_SV_ENG_SWITCH_ADBMODE()` handler.

**BUT**: The handler's `sendInstallMsg(0)` gate will block it — it resolves
`com.desaysv.INSTALL_PACKAGES` service via `PackageManager.resolveService()`, and the match
value comparison (`== 1`) fails because resolved services return match quality constants
(e.g., `MATCH_DEFAULT_ONLY = 0x00010000`), not `1`. The property is never set through this path.

**Broader implication**: Any VDB event can be injected from shell. This is a CAN bus injection
equivalent — attackers with shell access can spoof vehicle signals. Events worth exploring:
- `0x4413` — "Input AESKEY" (provision CMAC key to MCU)
- `0x7523` — "ClearScSk" (clear security state)
- `0x8556` — ADB status response event
- `0x0130001` — the reboot command (`desaysv_vdb_smoke_tester` target)

## 2. Root Services Without SELinux Domain Transition

| Service | Binary | Runs as | SELinux domain | Trigger |
|---------|--------|---------|----------------|---------|
| `uds_doip_server` | `/product/bin/uds_doip_server` | root | init (no seclabel) | `sys.boot_completed=1` |
| `desaysv_engineering` | `/vendor/bin/com.desaysv.ivi.engineering@1.0-service` | root | `desaysv_engineering` (no rules) | disabled; needs `ctl.start` |
| `car_vdb_adaptor_watchdog_alarm` | triggered service | root | unknown | `car.vdb.adaptor.watchdog.alarm.trigger` |
| `car_vdb_adaptor` | triggered service | root | unknown | `car.vdb.adaptor.start.trigger` |

The `desaysv_engineering` HIDL service runs as root with no SELinux policy rules at all — same
as `uds_doip_server`. It requires a `ctl.start` from system-level to activate, but once started
it has unconstrained root access.

## 3. Property-Based ADB Enable — All Paths

The `persist.sv.debug.adb_enable` property lives in SELinux context `persist_sv_svdebug`.
Only these domains can SET it:

| Domain | What runs in it |
|--------|-----------------|
| `svdebug` | DesaySV debug service |
| `system_app_30_0` | System-UID apps (SVEngMode, SVSetting, etc.) |
| `vendor_init_30_0` | Vendor init process |

**Shell cannot set it.** The `adbdctl` script is a no-op from shell.

**DoIP persistent_properties injection**: Since `uds_doip_server` runs in init context
with unconstrained file access, it can write to `/data/property/persistent_properties`
to inject `persist.sv.debug.adb_enable=1`. After reboot, init loads the file and
the property takes effect. This bypasses the entire EngMode UI flow.

## 5. DesaySVProjectService — Exported Services

- `ProjectionService`: exported, no permission
- `DesaySVProjectManagerService`: exported, `mediaProjection` foreground service type

Both run as `android.uid.system`. No `Runtime.exec()` or `SystemProperties.set()` paths
found. They handle phone projection (CarPlay/Android Auto) display management.

## 6. Crypto Material in Dump

- **RSA public key** (ADB auth): 4096-bit in `EncryptUtils.PUBLIC_KEY` — private key NOT in dump
- **AES-128 key** (EngSecKey.txt): `2377deae3106286889297b8aec0a157e` — HARDCODED in APK
- **CMAC key** (Version 3.0 password): stock `31697060FAA6B4D5A5AD1C4D9A3BD07A` — provisionable via USB
- **OTA upgrade RSA public key**: N,E pair in `etc_vendor/security_upgrade_public_key.txt`
- **Location/GNSS certs**: `cacert_location.pem` (IZat Cloud), `xtra_root_cert.pem` (Qualcomm XTRA)
- **No private keys found anywhere in the dump**

## 7. World-Writable Partitions

- `/vr` — `iflytek_chmod.sh` runs `chmod -R 777 /vr` at boot
  - Contains iFlytek voice recognition model/resource files only
  - No `.so` libraries or executables loaded from `/vr`
  - Not exploitable for code execution without replacing the voice recognition APK

## 8. UpperTester — Diagnostic Tool

`/vendor/bin/UpperTester` — aarch64 ELF with `system()` libc call.
- Creates TCP/UDP sockets, binds to configurable IP/port
- Runs `ifconfig eth0` commands via `system()`: `up`, `down`, `0.0.0.0`, and IP assignment
- Not started from any init script — manual diagnostic tool
- If executable from shell (labeled `cabin_all_exec` — same as `desaysv_vdb_emulator`),
  could potentially be used to manipulate network interfaces

## 9. Summary of New Attack Vectors

### Confirmed exploitable:
1. **DoIP root file write → persistent_properties injection → ADB enable after reboot**
   - No shell access needed, works over Ethernet
   - Write crafted `persistent_properties` file to `/data/property/`

### Needs further testing on live device:
2. **VDB inject from shell** — any VDB event injectable, but ADB-specific handler has a broken gate
4. **UpperTester** — network interface manipulation from shell

### Additional system-UID network services:
6. **FtpServer** (`com.desaysv.ftpserver`) — Apache FTPServer running as `android.uid.system`
   - Binds to `192.168.8.2:2121` (Ethernet interface)
   - User config from `/mnt/sdcard/user.config` with **cleartext** passwords
   - If write-enabled, provides system-UID file writes over FTP
   - FtpServiceReceiver auto-starts on boot

### Command injection in SVVDSUpgrade:
7. **SVVDSUpgrade** (`com.desaysv.ivi.vds.upgrade`) — system-UID OTA upgrade app
   - `g.b(path)` → `Runtime.exec("chmod -R 770 " + path)` — no shell quoting/escaping
   - `g.c(path)` → `Runtime.exec("chmod 664 " + path)`
   - `g.d(path)` → `Runtime.exec("chmod 777 " + path)`
   - `g.A(path)` → `sh -c "cd <path>; ls -al"` — shell metachar injection
   - Paths come from internal OTA staging (`/data/update/`), not direct intent extras
   - **If DoIP file write plants a symlink or crafted filename in the staging dir,
     the shell metacharacters in the filename execute as system UID**

### Blocked by SELinux:
5. **adbdctl from shell** — property context prevents shell from setting `persist_sv_svdebug`
6. **desaysv_engineering service** — requires `ctl.start` from system-level
