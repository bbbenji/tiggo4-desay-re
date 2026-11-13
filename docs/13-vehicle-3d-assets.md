# 13 — Vehicle 3D assets

Short finding, saves anyone a wasted hunt: **there is no polygon mesh of the car anywhere on the
head unit.** Every "3D car" in the HMI is **pre‑rendered 2D imagery** baked from 3D models that
were never shipped to the unit.

No `.obj` / `.fbx` / `.gltf` / `.glb` / `.dae` exists in any partition dump. The car imagery lives
in three `product-app` packages:

- **`MaterialVehicle.apk`** (~284 MB, ~5,009 PNGs) — generic `sedan_` / `suv_` ADAS feature
  animation flipbooks.
- **`MaterialVehicleT19FL.apk`** (~163 MB) — **this car** (T19 = Tiggo platform): interior renders
  (`img_atmosphere_car`, `img_setting_bg`), door / tailgate / hood open sequences, seat (`chair`),
  `ac_front_row`, a 90‑frame `middle_*` sequence, HUD/NFC backgrounds.
- **`MaterialVehicleT19FLHEV.apk`** (~213 MB) — hybrid energy‑flow / charge / drivetrain animations
  (`two_drive_*`, `four_drive_*`).

The **360 surround view has no 3D model on the head unit either** (see
[09](09-dvr-360-cameras.md)): the bird's‑eye composite is rendered upstream on the camera ECU and
streamed as video; the SA8155 only displays video + 2D PNG overlays. The DVR/360 native libs are a
video player (IJKPlayer + Skyworth SDK), with no birdview/avm/opengl tokens.

**Implication:** for any "3D car" visual work, composite the MaterialVehicle PNG sequences — don't
look for a renderable mesh on the IVI side. If a mesh exists at all, it's in the camera‑ECU
firmware, not in these dumps.
