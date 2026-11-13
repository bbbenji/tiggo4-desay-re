# 08 — Internal FTP server

The head unit runs an **Apache FtpServer** (`product-app/FtpServer`) bound to
**`192.168.8.2:2121`** on the internal vehicle network. It's a genuine privileged file‑access
surface: it exposes directories that the `adb shell` user (uid 2000) **cannot** read.

## Credentials & config

The real config (port, root, accounts) ships **encrypted** as `assets/encrypt.properties` (2400 B)
and is mirrored at `/mnt/sdcard/encrypt.properties`, decrypted at runtime by `FtpServiceDeamon.c()`.

- **Cipher:** AES/CFB/NoPadding, **zero IV**.
- **Key:** the ASCII string `2FFBF1FFE951709E123533CF2769FD36` (32 bytes → AES‑256).

Decrypt it yourself:

```bash
openssl enc -d -aes-256-cfb \
  -K $(printf 2FFBF1FFE951709E123533CF2769FD36 | xxd -p -c64) \
  -iv 0*32 -nopad < encrypt.properties
```

Decrypted accounts (hardcoded, platform‑wide):

| User | Password | Home | Write |
|---|---|---|---|
| `desaysv` | `sv2655888` | `/data/update` | yes |
| `avm` | `sv2655888` | `/data/avm` | yes |
| `admin` | `123456` | — | **disabled** (commented out) |
| `anonymous` | — | — | **disabled** |

`maxlogin` is unlimited. These are OEM/fleet credentials, recoverable by anyone from the shipped
APK; included per the owner's decision (see [00](00-safety-and-legal.md)).

## Confirmed live behavior (on‑car)

- **Login works:** `desaysv` / `sv2655888` → `230 User logged in, proceed`; server banner = Apache
  FtpServer; `PWD` = `/` which maps to `/data/update`.
- **Each account is chrooted to its home** — FTP `/` *is* the home directory, so `LIST /` (not
  `LIST /data/update`). Both `desaysv` (→`/data/update`) and `avm` (→`/data/avm`) log in and
  `LIST`/`NLST` succeed (150 → 226).
- The dirs were **empty** at test time (no OTA staged, no loose AVM files), but `writepermission =
  true`, so the path **can write** into `/data/update` (OTA staging) and `/data/avm` (AVM calib).
- **Privilege gap:** `adb shell ls /data/update` and `/data/avm` → *Permission denied*, but FTP
  (running at a privileged uid) serves them. So FTP is a privileged R/W path into OTA staging and
  AVM calibration over the internal net — something the shell user can't otherwise reach.

## Reachability

- Reachable on the **internal `192.168.8.x`** net only. From a laptop on the unit's Wi‑Fi
  (`172.18.155.0/24`), `:2121` SYN is dropped (firewalled on `wlan0`), `:21` is refused, and
  `192.168.8.2` isn't routable. You need a PASV‑capable client **on the internal net** (an
  on‑device client, or something bridged onto `192.168.8.x`).
- Practical on‑device exercise pattern (since `mkfifo`/coproc are SELinux‑blocked/fragile):
  `tail -f <controlfile> | nc` for the control channel + a second `nc` for the PASV data channel.

## Why it matters

Writing a signed `.cbf` into `/data/update` is how a local OTA would stage — this FTP path is a way
onto that staging area without root. Treat it with the same care as the OTA UI
([00](00-safety-and-legal.md)): staging and flashing firmware can brick the unit.
