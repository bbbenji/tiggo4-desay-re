# 09 — DVR / 360 cameras

Goal that drove this: build an app that extends the **360‑camera DVR**. The short answer is that true‑360 capture is hard because the bird's‑eye
composite is not produced on the Android SoC at all.

## Architecture (confirmed on‑device)

- **8 raw camera inputs** at `/dev/video51..58` (V4L2, owned `system:camera`) = the 360/AVM set.
  Not accessible to untrusted apps.
- **Standard Camera2 exposes only 1 camera** (rear, backward‑compatible) — so your own Camera2 code
  can't get true 360.
- **CheryDVR** (`com.skyworthauto.dvrchery`, `/product/app/CheryDVR/CheryDVR.apk`, ~30 MB, v5.0.x)
  is the DVR engine. It runs as **`sharedUserId=android.uid.system`**, which is how it reaches all
  cameras (holds CAMERA / RECORD_AUDIO / WRITE_SETTINGS). It's a **networked‑camera app**
  (`RemoteCamApp`, Wi‑Fi perms) → live frames arrive over Wi‑Fi (RTSP/proprietary), not a local
  binder.
- The real 360 source is a **SOME/IP CommonAPI service** `svp.camera.Camera` on the **peer SoC**
  (`192.168.8.1`); the client proxy is `libCamera_someip.so`. The **bird's‑eye view is rendered
  upstream on the camera ECU and streamed as video** — the SA8155 only displays video + 2D PNG
  overlays (see [13](13-vehicle-3d-assets.md)).

## What a 3rd‑party app can reach

CheryDVR exposes:

- **`LiveViewActivity`** — `MAIN`/`LAUNCHER`. Just launching the package opens the 360 live view.
  **This is the quick win** — one `startActivity` and the DVR UI is up.
- **Exported `CameraService`** — action `skyworthauto.intent.action.camera`, process
  `:cameraService`, **no signature permission** → a 3rd‑party app can `bindService` it. But
  `onBind` returns an **obfuscated** Binder (`a.h.b.b.e` extending `a.h.a.b$a`, implementing
  `a.h.b.a.a.i$a`). The interface `a.h.b.a.a.i` is a generic IPC **channel** (open/close/read/write
  `a.h.b.a.c.e` packets + a callback) — a layered, obfuscated **Skyworth IPC SDK**, not per‑command
  AIDL. Real commands = serialized packets over that channel.
- **`DvrService`** (engine) and broadcast **`DvrAppReceiver`** (action `skyworthauto.intent.action`,
  extra `"dvr"` — only value seen = `"launch"`, i.e. launch‑only, not a rich control API).

Native stack is a **video player**, not a renderer: IJKPlayer (`libijkplayer`/`libijkffmpeg`/
`libijksdl`) + Skyworth `libcybersdk.so` + `libscm.so` (GOST/crypto comms). No birdview/avm/opengl
tokens anywhere.

## Build path (if you want real control)

1. Extract the Skyworth SDK (`a.h.*` + `com.skyworthauto.sdk.*`) into a bundleable lib (dex2jar →
   prune, like `desay-vdb.jar`).
2. Decode the `a.h.b.a.c.e` packet format + command codes by RE'ing `LiveViewActivity`'s
   record/snapshot/view handlers (readable context/resources).
3. Build a client that binds `CameraService`, opens the channel, issues commands → test on car.
4. Heavier: find whether the SDK exposes a live preview `Surface` to clients → stream → on‑device
   AI. (Big job; low payoff vs. just launching the app.)

A low‑effort fallback that needs no SDK: read the DVR's USB clips off the filesystem and
upload/manage/overlay them.

## Note

A second DVR engine, `CallistoVDR`, is also installed but unexamined.
