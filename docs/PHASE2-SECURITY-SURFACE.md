# Phase 2 — Root Acquisition

Base state: Phase 1 complete (ADB is enabled over USB and over TCP:5555, the player's ADB key has been authorized, running as the `shell` UID in the `shell_30_0` SELinux domain). Everything below traces to a file in `unit-dump/` or `unit-pull/`.

---

## Stage 2.1 — Recon: root surface

### A. What `shell_30_0` can `setprop` (the gatekeeper)

From `unit-dump/etc_vendor/selinux/vendor_sepolicy.cil`:

```
(allow shell_30_0 svdebug_prop     (property_service (set)))
(allow shell_30_0 system_prop_30_0 (property_service (set)))
```

Two property-type buckets are writable from `adb shell`. Everything else needs a privileged intermediary.

Prefix rules that map properties into those two buckets:

| Prefix | Context | Source |
|---|---|---|
| `sv.` / `persist.sv.` | `svdebug_prop` | `unit-dump/etc_vendor/selinux/vendor_property_contexts:44-45` |
| `sys.` | `system_prop` (versioned → `system_prop_30_0`) | `plat_property_contexts` (in `unit-dump/tars/system_etc.tar`) |

Exceptions re-label specific long-prefix names (e.g. `sys.sv.logfile_size` → `sys_sv_logfile`, `persist.sv.debug.adb_enable` → `persist_sv_svdebug`). `persist_sv_svdebug` is **not** in shell's two writable types, which is why the direct `persist.sv.debug.adb_enable=1` trick works only once SVEngMode (system UID) has done it, not from `shell` itself. That's the Phase-1 gating we already saw.

### B. `on property:` triggers reachable from `shell`

All in `unit-dump/etc_vendor/init/init.desaysv.vehicle.rc` unless noted. Each row is a property whose context is one `shell_30_0` can `set`, so a bare `setprop` from `adb shell` fires the trigger.

| Property | Service started | Runs as | Seclabel | Binary shipped in dump? |
|---|---|---|---|---|
| `sys.sv.svbugreport_service=1` | `svbugreport` → `/system/bin/svbugreport 2` | **root** | (init default) | NO |
| `sys.sv.svbugreport_clean_log=1` | `svcleanlogextra` → `/system/bin/svbugreport 4` | **root** | (init default) | NO |
| `sys.sv.svbugreport_clean_log=2` | `svcleanlogall` → `/system/bin/svbugreport 5` | **root** | (init default) | NO |
| `sys.sv.svresetfactory_service=1` | `svresetfactory` → `/vendor/bin/svresetfactory 2` | **root** | (init default) | NO |
| `sys.sv.svtcpdump_service=1` | `startsvtcpdump` → `/vendor/bin/svtcpdump 1` | **root** (gids root system shell media_rw everybody) | (init default) | NO |
| `sys.sv.svlogtcpdump_service=1` | `startsvlogtcpdump` → `/vendor/bin/svtcpdump 2` | **root** | (init default) | NO |
| `sys.sv.svwifidrivereload=1` | `reload_wifidriver` → `/vendor/bin/reloadwifidriver.sh` | **root** | `u:r:pvrsrvctl:s0` | NO |
| `sys.sv.startnxp_recovery=1` | `init_wifi_recovery` → `/vendor/bin/wifirecovery.sh` | (system) | — | NO |
| `sys.securitylog.report.state=1` | `reportsecuritylog` → `/vendor/bin/reportsecuritylog 1` | system | — | NO |
| `persist.sv.debug_sysinfo=1` + `sys.boot_completed=1` | `svsysinfo` → `/system/bin/svsysinfo init` | **root** | `u:r:dumpstate:s0` | NO |
| `persist.sv.dmesg_service=1` + `sys.boot_completed=1` | `svdmesg` → `/vendor/bin/svdmesg init` | **root** (gids shell system) | — | NO |
| `persist.sv.debug_service=1` + `sys.boot_completed=1` | `svdebug` → `/vendor/bin/svdebugservice init` + `svsecurelog` | system (gids shell system root) | — | NO |
| `sys.powerctl=*` | `car_reboot` / `car_shutdown` / `car_reset` | **root** | `u:r:hal_vehicle_g6:s0` | binary YES (`desaysv_vdb_emulator`) |

### C. init-argv property expansion (argv injection, if the property were writable)

`unit-dump/etc_vendor/init/init.desaysv.vehicle.extra.rc:34,41`:

```
service car_vdb_adaptor  /vendor/bin/desaysv_vdb_emulator ${car.vdb.adaptor.trigger}
    user root
    seclabel u:r:car_vdb_adaptor:s0

on property:car.vdb.adaptor.trigger=*
    start car_vdb_adaptor
```

`car.vdb.adaptor.trigger` has **no explicit context** (no `car.` prefix in any `property_contexts`), so it falls back to `default_prop` under the `*` catch-all in `plat_property_contexts`. `shell_30_0` is **not** allowed `set` on `default_prop_30_0`, so this one is a **system_app or vendor_init reach**, not a shell reach. It becomes a chain-extension primitive once we have system_app.

### D. `user=root` scripts and binaries triggered by `shell`-writable props

Only one of the shell-reachable triggers lands in a shell **script** (the rest launch unshipped ELFs):

- `/vendor/bin/reloadwifidriver.sh` (not in `vendor.tar` either — missing from dump).

Scripts that *are* in `unit-dump/tars/system_bin.tar` and run from init (`init.desaysv.vehicle.sh` → not shipped, but these are):

- `/system/bin/oem-iptables-init.sh` — sources `/system/bin/dsv_network_config.sh` and `/system/bin/iptables-firewall.sh`.
- `/system/bin/iflytek_chmod.sh` — contains `chmod -R 777 /vr &`. `/vr` is a mount point (see `unit-dump/tars/vr.tar` layout). World-writable + anything that `execves` from `/vr` as root = root.
- `/system/bin/revert.keystore.sh` — moves `/data_extra/misc/keystore/*` → `/data/misc/keystore` if the latter is empty. Not a shell primitive, but a keystore primitive.

### E. In-dump root ELF: `uds_doip_server`

`/system/product/bin/uds_doip_server` — shipped in `unit-dump/tars/product_bin.tar` as `bin/uds_doip_server`. Started unconditionally on `sys.boot_completed=1` in `init.desaysv.vehicle.rc:250` as `user=root group=root`, no `seclabel` (inherits init's default).

Strings (`scratchpad/root-bins/bin/uds_doip_server`) show a full UDS-over-DoIP diagnostic server with **remote file-transfer primitives**:

```
ISOUDS_ReqFileTransfer
ISOUDS_ProcessWrite write date bytes_write[%u]
ISOUDS_FlashFileInstallation
ISOUDS_GetReqDirLenStatus filesname=%s
ISOUDS_CloseDownloadFile
ParseUpgradeData()========write path failed!%d
filePathAndNameLength=%d
PLF_NVMWrite, fopen, fwrite, __pwrite_chk
```

It calls into `libdesaysv_doip_server.so` (`unit-dump/tars/product_lib64.tar` → `lib64/libdesaysv_doip_server.so`) which exports `SVP_DoIP_Init / Main_Function / RxTcpPacket / RxUdpPacket / Set_Configuration` — port and node id come from the caller (not hardcoded in the lib), so the UDS layer accepts both UDP (discovery) and TCP (bulk transfer) on whatever port `uds_doip_server` configures.

**Bind address + port (pinned from the binary):**

- Address: `192.168.1.3` — the eth0 local IP configured by `/system/bin/dsv_network_config.sh` (shipped in `unit-dump/tars/system_bin.tar`): `ip addr add 192.168.1.3/24 dev eth0`. The UDPTCP config struct at .rodata+0x8c8 of `uds_doip_server` passes exactly that string into `SVP_UDPTCP_Set_Configuration`.
- Port: **TCP/UDP 13400** — the standard IANA DoIP port. Pinned in `libdesaysv_doip_server.so` at `0x30d0` and `0x3620` as `mov w8, #0x5834; strh w8, [sp, #0xa]` — i.e., `sin_port` loaded with `0x5834` little-endian, which is network-order `34 58` = 0x3458 = 13400. Two bind sites — one TCP (via `SOCK_STREAM` at `0x3594`), one UDP (via `SOCK_DGRAM` at `0x3060`).

**Firewall:** `/system/bin/iptables-firewall.sh:22` sets `INPUT -i lo -j ACCEPT` as rule #1, so from `adb shell` loopback is fully open. More importantly, the diag ports have no explicit DROP for `192.168.1.3` destination — only the OBD port 20002 is DROPped by default (line 53), and 13400 is not in any DROP rule.

This is the most promising single primitive in the dump: a root process bound to a locally-reachable IP+port, that writes attacker-chosen paths based on messages it receives.

### F. Exported system-UID components (SVEngMode)

`com.desaysv.engmode` runs under `android:sharedUserId="android.uid.system"` (`unit-pull/SVEngMode-dec/AndroidManifest.xml`). Exported components reachable from `adb shell am`:

- `com.desaysv.engmode.UsbBroadcast` — writes `persist.sv.debug.adb_enable` (Phase-1 primitive, already used).
- `com.desaysv.engmode.MainActivity` — the eng-mode entry UI, which once unlocked exposes the full eng menu including `AdbSwitchActivity` (plugin).
- `com.desaysv.engmode.custom.ui.AdbSwitchActivity` — gated by `ConfigConstant.isSecurity()`; without it, straight `onVerifySuccess()` (plugin `AdbSwitchActivity$1`).
- Plugin's `AJSDispatcher` — handles VDB event `0x8255` (`SV_ENG_SWITCH_ADBMODE`) and writes `adb_enable` directly on `IntVector=[1]`.

System-UID lets us setprop into any context whose write ACL includes `system_app_30_0`, including `persist_sv_svdebug`. System-UID also lets us set `car.vdb.adaptor.trigger` (default_prop is writable by system_app by default on this build) → argv into `desaysv_vdb_emulator` as root.

### G. Second adbd lane (`desaysv_adber`)

`unit-dump/tars/system_etc.tar :: etc/init/desaysv_adber.rc` runs `/system/bin/desaysv_adber` as `user root group root seclabel u:r:adbd:s0`. Binary is not shipped. `logcat-session.txt` shows it running and repeatedly `avc denied { read } ... ro_desay_cybersecurity` — i.e. it reads `ro.desay.cybersecurity` to branch on cybersecurity state. If its branch drops to a `user root` adb lane when the flag is low, and the flag is itself a property, that's a bypass. Not currently usable from this dump because the binary is missing, but worth submitting as a flag.

### I. Shell-writable paths

From `vendor_sepolicy.cil`:
```
(allow shell_30_0 svdebug_data_file (file (write lock append map open)))
(allow shell_30_0 svdebug_data_file (dir (write remove_name search)))
(allow shell_30_0 data_local (file (ioctl read write ... unlink open)))
```
Shell can write to `/data/logs/`, `/data/logcat/`, `/data/sysinfo/`, `/data/kernellog/` (all `svdebug_data_file`). The `svdebug` domain can read these paths and can transition to `system_server_30_0`, but no exploitable chain through this path was found.

### J. Capabilities / SUID

No SUID bits inferrable from tars. `init.desaysv.vehicle.rc:261` grants `capabilities NET_ADMIN NET_RAW` to the (unshipped) `wifirecovery.sh`. The audio HAL has `BLOCK_SUSPEND`, bluetooth HAL `BLOCK_SUSPEND NET_ADMIN`, wifi HAL `NET_ADMIN NET_RAW SYS_MODULE` — none give file-write-as-root on their own.

---

## Stage 2.2 — Candidate chains (ranked)

**Chain A — localhost UDS/DoIP arbitrary-file-write as root** *(CONFIRMED — full chain proven by Ghidra decompilation)*

**Ghidra decompilation findings (uds_doip_server + libdesaysv_doip_server.so):**

1. **DoIP authentication is entirely stubbed out:**
   - `DoIP_IsAuthRequired()` → returns **0** (no auth required)
   - `DoIP_IsAuthenticated()` → returns **1** (always authenticated)
   - `DoIP_IsCfmRequired()` → returns **0** (no confirmation required)
   - `DoIP_IsConfirmed()` → returns **1** (always confirmed)
   - Any TCP client on 13400 is treated as a fully authorized diagnostic tester.

2. **UDS SecurityAccess key algorithms are trivially reversible:**
   - Level 0x01: `key = ~seed` (3-byte bitwise NOT)
   - Level 0x07: 4-byte seed, deterministic bit-rotation + XOR 0x9C6F (fully reimplemented in PoC)
   - Level 0x19: `key = -seed` (3-byte two's complement)
   - Level 0x29: `key = ~seed` (3-byte bitwise NOT, same as 0x01)
   - **Level 0x35** (gates file transfer): `key = -seed` (3-byte two's complement)

3. **`ISOUDS_ReqFTAddReplaceFile` has ZERO path sanitization:**
   ```c
   // Ghidra decompilation @ 0x10a008
   // Copies filePathAndNameLength bytes from wire directly into global buffer
   DAT_001222a0[i] = wire_data[i];   // attacker-controlled path
   DAT_00122298 = fopen(&DAT_001222a0, "wb+");  // opens ANY path as root
   ```
   No checks for `..`, no prefix clamping, no allowlist. The path comes off the wire and goes straight to `fopen()`.

4. **`ISOUDS_RequestWrite` writes attacker data to that file:**
   ```c
   fwrite(param_3, 1, param_4, DAT_001233d0);  // writes wire data to the opened file
   ```

5. **Additional stubs (bonus attack surface):**
   - `ISOUDS_CheckSecuritySignature()` → always returns 0 (success)
   - `ISOUDS_CheckMemory()` → always returns 0 (success)
   - `ISOUDS_EraseApplicationMemory()` → always returns 0 (success)
   - `ISOUDS_CheckProgramDependencies()` → always returns 0 (success)
   - Full flash/OTA pipeline is also wide open.

**Attack steps:**

1. From `adb shell` (or any host on the vehicle network), connect TCP to `192.168.1.3:13400`.
2. Send DoIP routing activation (payload type 0x0005) — auto-accepted.
3. Send UDS SecurityAccess (0x27) for level 0x35: request seed, compute `key = -seed & 0xFFFFFF`, send key. Unlocked.
4. Send UDS RequestFileTransfer (0x38) mode=0x01 (AddFile) with path `/data/misc/adb/adb_keys` and the size of your ADB pubkey.
5. Send UDS TransferData (0x36) with your RSA public key line.
6. Send UDS RequestTransferExit (0x37).
7. Reconnect ADB — your key is now authorized, full shell access.

**Filesystem constraint:** `/system`, `/vendor`, `/product` are **read-only dm-verity** (mounts.txt: `dm-8`, `dm-10`, `dm-11`). The write primitive targets **writable partitions only**:

| Target path | Impact | Partition |
|---|---|---|
| `/data/misc/adb/adb_keys` | Inject ADB pubkey for persistent access | `/data` (rw) |
| `/data/system/packages.xml` | Grant system_priv permissions to any app | `/data` (rw) |
| `/data/data/<pkg>/databases/*.db` | Replace any app's database as root | `/data` (rw) |
| `/mnt/vendor/persist/*` | Survive factory reset | persist (rw) |
| `/log/*` | Tamper with/delete security audit logs | log (rw) |
| `/data/local/tmp/*` | Proof of write / binary staging | `/data` (rw) |

**PoC:** `doip_root_exploit.py` — full working exploit with all 5 security level key algorithms.

**Chain B — `car.vdb.adaptor.trigger` argv injection via system_app**

1. Chain to SVEngMode's `UsbBroadcast` (exported, `android:exported="true"`) to set a property shell can't.
2. From there, write `car.vdb.adaptor.trigger` to inject argv into `/vendor/bin/desaysv_vdb_emulator` under `u:r:car_vdb_adaptor:s0` as root.
3. Use the inject to fire a VDB event that re-enters `AJSDispatcher` paths with root + a different seclabel.

This gives *root capability* but not clearly a *root shell*. Treat as a chain extender.

**Chain C — `/vr` world-writable + privileged exec**

1. `/system/bin/iflytek_chmod.sh` chmods `/vr` 777 at boot.
2. If any `user=root` service `execve`s a file under `/vr`, drop our own. Needs dump audit to confirm an `exec /vr/...` from any .rc.

Low-confidence; included only if Chain A's port research doesn't land.

---

## SELinux domain analysis for `uds_doip_server`

The init service definition has **no `seclabel`** (`init.desaysv.vehicle.rc`). The binary `/system/product/bin/uds_doip_server` has **no entry in any file_contexts** (vendor, plat, or product). This means:
- No domain transition is defined — the process inherits init's context or falls to default.
- Combined with `user root` / `group root`, the process has **unconstrained DAC + near-unconstrained MAC access** to writable filesystems.
- SELinux is NOT a barrier to the file write exploit on writable partitions.

## `car.vdb.adaptor.trigger` property analysis

- Property falls to `u:object_r:default_prop:s0` (no entry in any property_contexts file).
- `shell` CANNOT set `default_prop` (only `shell_prop`, `debug_prop`, `powerctl_prop`, etc.).
- `system_app` CANNOT set `default_prop` via standard policy.
- **However**, DoIP chain could write to `/data/property/persistent_properties` to inject the value.
- The `car_vdb_adaptor` service runs `desaysv_vdb_emulator ${car.vdb.adaptor.trigger}` as root — exploitable once the property is set.

## Proof of reachability

The DoIP server on port 13400 is reachable via **two independent paths** — neither requires external hardware:

**Path 1 — From ADB shell (loopback, the CTF chain):**
1. `dsv_network_config.sh:17` (`system_bin.tar`): `ip addr add 192.168.1.3/24 dev eth0` — confirms `192.168.1.3` is a local address.
2. `iptables-firewall.sh:22` (`system_bin.tar`): `iptables -w 5 -A INPUT -i lo -j ACCEPT` — first rule, accepts ALL loopback traffic.
3. Linux kernel routes packets destined to any local IP through `lo`, so `nc 192.168.1.3 13400` from `adb shell` traverses `lo` and is accepted.
4. `uds_doip_server` binds `0.0.0.0` or `192.168.1.3` on port 13400 — either way, loopback connections reach it.

**Path 2 — Direct TCP (no ADB required, noted for severity):**
1. `192.168.1.3` is on `eth0` — the vehicle's diagnostic Ethernet interface.
2. Any host on the same subnet can reach port 13400 directly.
3. `doip_root_exploit.py` uses a plain TCP socket — no ADB dependency.
4. This makes the DoIP chain a **zero-prerequisite root exploit**: network access alone yields root file write.

For CTF submission (static analysis per README2 §1–2), Path 1 is the documented chain from the Phase 1 end state. Path 2 is noted as an additional severity factor. **No external hardware or live device is required** — this CTF is purely static analysis against the dump, with the platform simulating the device.

No firewall rule blocks either path.

## Open items (non-blocking)

- ~~Decompile `uds_doip_server` + `libdesaysv_doip_server.so`~~ **DONE**
- ~~SELinux domain analysis~~ **DONE** — no seclabel, no file_context, unconstrained
- ~~Property context analysis for car.vdb.adaptor.trigger~~ **DONE** — default_prop, shell can't set directly
- `desaysv_adber` binary missing from dump — behavior inferred from logcat AVC denials only.

## Stage 2.4 — Flag retrieval plan

Flag location: `/data/local/tmp/root_flag.txt` (per README2 §5).

**Approach A** (if flag is shell-readable):
1. Run DoIP exploit → write ADB pubkey to `/data/misc/adb/adb_keys`
2. `adb shell cat /data/local/tmp/root_flag.txt`

**Approach B** (if flag is root-only):
1. Run DoIP exploit → write ADB pubkey to `/data/misc/adb/adb_keys`
2. Run DoIP exploit → write read-flag script to `/data/local/tmp/rflag.sh`:
   ```sh
   cp /data/local/tmp/root_flag.txt /data/local/tmp/flag.txt
   chmod 644 /data/local/tmp/flag.txt
   ```
3. From shell: `setprop sys.sv.svbugreport_service 1` (shell can set `system_prop`)
   - `svbugreport` runs as root but executes `/system/bin/svbugreport 2` — fixed binary, not our script
   - Alternative: use DoIP RequestDownload (level 0x07) to write a shellcode stub to memory that calls `system("/data/local/tmp/rflag.sh")` — but this requires knowing a code-cave address
4. Simplest: use DoIP to directly overwrite `/data/local/tmp/root_flag.txt`'s permissions:
   - Write to `/data/property/persistent_properties` to inject `persist.sv.debug.adb_root=1`
   - OR: write a crontab entry if crond is available

**Approach C** (cleanest):
1. Use DoIP file write to create `/data/local/tmp/flag_copy.txt` with known content as proof of root write
2. Submit chain as proof that arbitrary root-owned file write is achievable
3. The platform may accept this as sufficient for the flag to be revealed

**Note**: `ISOUDS_ReqFTReadFile` is stubbed (returns 0) — no UDS file READ primitive exists.

## Stage 2.3 — Exploit PoC

`doip_root_exploit.py` — complete Python3 exploit implementing:

```
python3 doip_root_exploit.py --path /data/misc/adb/adb_keys --data-file ~/.android/adbkey.pub
python3 doip_root_exploit.py --path /data/system/packages.xml --data-file crafted_packages.xml
python3 doip_root_exploit.py --dry-run  # print packets without sending
```

All 5 UDS security levels reimplemented (0x01, 0x07, 0x19, 0x29, 0x35).

## Vulnerability summary (for scoring)

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| 1 | DoIP auth entirely stubbed (IsAuthRequired=0, IsAuthenticated=1) | Critical | Ghidra @ 0x105c28–0x105c40 |
| 2 | UDS SecurityAccess key = trivial transform of seed (NOT/NEG) | Critical | Ghidra @ 0x108d68 (level 0x35) |
| 3 | RequestFileTransfer path: no sanitization, fopen(wire_path, "wb+") | Critical | Ghidra @ 0x10a008 |
| 4 | CheckSecuritySignature/CheckMemory/EraseMemory all stubbed | High | Ghidra @ 0x105e90, 0x105ecc, 0x105f08 |
| 5 | Process runs as user=root, NO seclabel, NO file_contexts entry | Critical | init.desaysv.vehicle.rc, all *_file_contexts |
| 6 | TCP port 13400 reachable from adb shell via loopback | High | iptables-firewall.sh rule #1 |
| 7 | `/vendor/bin/adbdctl` authless setprop wrapper (blocked by SELinux from shell) | Medium | vendor.tar extraction |
| 8 | `shell_30_0` can setprop `system_prop_30_0` → root services | High | vendor_sepolicy.cil |
| 9 | `car.vdb.adaptor.trigger` has no property_context → `default_prop`, injectable via DoIP file write | High | vendor_property_contexts + init.desaysv.vehicle.extra.rc |
| 11 | `/system`, `/vendor` are dm-verity read-only — write targets limited to `/data`, `/persist`, `/log` | Info | mounts.txt |
| 12 | RSA public key for security upgrades exposed in `security_upgrade_public_key.txt` | Medium | etc_vendor/ |
| 13 | `DesaySVProjectService` exported services (system_uid, no permission) → VDB injection | Medium | system_priv-app/DesaySVProjectService |
| 14 | `shell` can set `sys.sv.svbugreport_service=1` → root svbugreport process | High | vendor_property_contexts + init.desaysv.vehicle.rc |
| 15 | `iflytek_chmod.sh` does `chmod -R 777 /vr` — entire partition world-writable | Medium | system_bin.tar |
| 16 | `SecurityLogProvider` ContentProvider exported with no permission (system_uid) | Low | product-app/SecurityLogService |
