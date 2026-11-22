# 01 — Hardware & platform

## The head unit

The Tiggo 4 (2025) IVI is a **DeSay SV** unit on a **Qualcomm SA8155P** (the "msmnile_gvmq"
automotive Snapdragon). It runs Android 11 (SDK 30) as a **production `user` build**.

Identity (from `getprop`):

| Property | Value |
|---|---|
| `ro.product.model` | `DesaySV` |
| `ro.product.device` | `msmnile_gvmq` |
| `ro.product.name` | `g7ph_t22_int` |
| `ro.product.cpu.abi` | `arm64-v8a` |
| `ro.build.version.sdk` | `30` |
| `ro.build.type` | `user` |
| head‑unit SoftAP SSID | `desay-g7ph-xxxxx` *(suffix redacted)* |

The platform is a **hypervisor guest**: the Android we see is one VM on the SA8155, with a peer
SoC/MCU domain next to it. The two talk over an internal automotive‑Ethernet fabric.

## Internal networks

Two internal networks matter:

- **`192.168.8.0/24` — the vehicle fabric.**
  - `192.168.8.2` = this Android IVI node.
  - `192.168.8.1` = the peer SoC / MCU gateway. This is where the **MCU control socket**
    (`:10004`, see [03](03-mcu-wire-protocol.md)) and an unexplored **MCU HTTP service**
    (`:60004`) live, and where the SPI‑to‑Ethernet vehicle‑bus bridge terminates.
  - The internal **FTP server** listens on `192.168.8.2:2121` ([08](08-ftp-server.md)).
- **`192.168.225.0/24` — the T‑Box RNDIS/NAT link.** `usb0` on the IVI is `192.168.225.10/24`
  with gateway `192.168.225.1` (the T‑Box). This is the *active default route* and has an
  INTERNET capability, but the T‑Box has no cellular service, so it goes nowhere
  ([10](10-tbox-telematics.md)).

Inter‑SoC services ride **CommonAPI / vSOMEIP** (automotive Ethernet), SOME/IP service discovery
on multicast port **30590**, unicast from `192.168.8.2`. Client proxy libs pulled from the unit:

- `svp.camera.Camera` → `libCamera_someip.so` — the real 360/AVM camera source ([09](09-dvr-360-cameras.md))
- `svp.display.DisplayController` → `libDisplayController_someip.so`
- `svp.upgrade.Upgrade` → `libUpgrade_someip.so`
- `dms.DMS` → `libDMS_someip.so` — driver monitor
- `svp.common.CommonMessage` → `libCommonMessage.so`

## Cameras

- 8 raw V4L2 nodes `/dev/video51..58`, owned `system:camera` — the AVM/360 camera set, **not**
  reachable by untrusted apps.
- Standard Camera2 (`media.camera`) exposes only **one** logical camera (rear, backward‑compatible).
- AIS camera map (`etc_vendor/ais.camera.xml`): id0 = **DMS** (driver monitor), id1 = **OMS**
  (occupant monitor), id2 = **DVR** (dashcam).
- HAL = Qualcomm **QCarCam** (`vendor.qti.automotive.qcarcam@1.0`).

See [09](09-dvr-360-cameras.md) for how the 360 stack is actually composed (hint: the bird's‑eye
view is rendered on the camera ECU and streamed as video; the SA8155 only displays it).

## Security posture

- **No leaked private keys/keystores** were found in any APK. The only embedded key material is
  public: `SVAssistant/assets/rsa_public_key.pem`, GPS‑assist location certs
  (`cacert_location.pem`, `xtra_root_cert.pem`), and a firmware‑verify public key
  (`security_upgrade_public_key.txt`).
- `/system` is read‑only **dm‑verity**; AVB is locked.
- `SVPermissionController` gates the VDB permission; `SVEngMode` holds the engineering menu and
  the ADB‑enable path.
- **Root is not attainable** on this unit by any method found: locked AVB, hypervisor guest,
  production `user` build, and the engineering menu exposes no root path. This bounds everything
  else in this repo — all techniques here are **non‑root, non‑platform‑signed**.

## Partitions / corpus

A full offline corpus (~3.2 GB) was pulled for remote analysis: `product-app/` (49 DeSay + Chery
APKs incl. CheryDVR, all `SVVDS*`, SVHvac, SVVehicleSetting, SVEngMode, MaterialVehicle),
`product_priv-app` / `system_priv-app` / `system_app` (SVLauncher, SVSetting,
DesaySVProjectService, PlatformAdapter), 50 framework jars (incl. `android.car.jar`), the SELinux
policy (`seapp_contexts`/`sepolicy`), and the vendor/product config trees. Not pullable without
root: `/vendor/app`, `/vendor/lib64/*` (the `libdesaysv_*` HAL libs + `idenauth`).
