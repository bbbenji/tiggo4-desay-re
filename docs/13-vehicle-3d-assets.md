# 13 — Vehicle 3D Assets & The Interactive 360° Car Illusion

## Executive Summary

When sitting in the Chery Tiggo 4 (2025) and dragging a finger across the headunit display, **the driver sees a crisp 3D vehicle that spins around freely in a full 360° circle**, complete with interactive door, sunroof, and tailgate animations.

Naturally, one would expect to find a 3D polygon mesh (`.obj`, `.fbx`, `.gltf`, `.glb`, or `.dae`) and an OpenGL/Vulkan 3D rendering pipeline somewhere in the Android partition dump.

**However, there is no 3D polygon mesh anywhere on the headunit.**

The "3D model you can spin around freely" is an elaborate **interactive 360° frame-sequence flipbook rendered on an Android `TextureView`**. The headunit swaps between **91 pre-rendered 2D PNG frames** captured at 4° intervals ($90 \times 4^\circ = 360^\circ$) in response to horizontal touch drag deltas.

This document breaks down the illusion, the asset structure, the touch interaction pipeline, and the underlying IPC architecture.

---

## 1. How the "Spin Around Freely" Illusion Works

```
User Touch Drag (X-Delta)
          │
          ▼
QuickSettingFragment / onTouchEvent(MotionEvent)
          │
          ▼
CarModelManager.getInstance().onTouchEvent(ev)
          │
          ▼  Calculates rotation delta / PresetAngle
CarModelProxy ──(AIDL: ICarModel)──► FrameAnimTextureView (TextureView @id/sfv_car)
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      ▼                                               ▼
         Base Vehicle Angle (0°..360°)                  Synchronized Overlays (0°..360°)
        `middle_0_left.png` (0°)                       `edge_left_0_left.png` (Ambient Light)
        `middle_1_left.png` (4°)                       `blow_feet_left_0_left.png` (HVAC Air)
        ...                                            ...
        `middle_90_left.png` (360°)                    `edge_left_90_left.png`
```

### The 91-Frame 360° Rotational Sequence
The core asset responsible for the spinning vehicle is located in `/system/product/app/MaterialVehicleT19FL/MaterialVehicleT19FL.apk` (T19 = Chery Tiggo platform, FL = Facelift):

- Files: `res/drawable/middle_0_left.png` through `res/drawable/middle_90_left.png`
- Count: Exactly **91 discrete frames**
- Resolution: $514 \times 250$ pixels (8-bit grayscale + alpha channel)
- Angular Resolution: $360^\circ / 90 \text{ intervals} = 4.0^\circ$ per frame

As your finger drags across the touch surface, the gesture detector tracks the horizontal $X$-delta, computes the current vehicle heading ($0^\circ \text{ to } 360^\circ$), and computes the target frame index:

$$\text{frame\_index} = \left\lfloor \frac{\text{angle}}{4.0} \right\rfloor \pmod{90}$$

Rendered at 60 FPS on hardware-accelerated Android `TextureView`, the 4° frame transitions are imperceptible to the human eye, creating the convincing tactile sensation of manipulating a live 3D polygonal mesh in real time.

---

## 2. Multi-Layered Rotational Compositing

The car is not just a single spinning image; it supports dynamic vehicle states (ambient lighting, HVAC airflow, open doors, sliding sunroof, raised tailgate). To maintain the illusion while rotating, every dynamic layer is rendered across the exact same 91 rotational angles:

| Layer Prefix | Frame Count | Resolution | Function |
| :--- | :--- | :--- | :--- |
| `middle_*_left.png` | 91 frames (0–90) | $514 \times 250$ | Base car body rotating $0^\circ \to 360^\circ$ at $4^\circ$ steps |
| `edge_left_*_left.png` | 91 frames (0–90) | $450 \times 250$ | Left ambient interior/exterior lighting edge synced to angle |
| `edge_right_*_left.png` | 91 frames (0–90) | $450 \times 250$ | Right ambient interior/exterior lighting edge synced to angle |
| `blow_feet_left_*_left.png` | 91 frames (0–90) | $450 \times 250$ | Driver footwell HVAC airflow stream synced to angle |
| `blow_feet_right_*_left.png` | 91 frames (0–90) | $450 \times 250$ | Passenger footwell HVAC airflow stream synced to angle |

### State Transition Animations (Non-Rotational)
When a specific feature is toggled (e.g., popping the tailgate or sliding the sunroof), the HMI smoothly transitions or snaps the vehicle to a preset angle (e.g., 3/4 rear perspective for tailgate, top/side perspective for sunroof) and plays a dedicated linear animation sequence:

- **Tailgate (`tailgate_0.png` to `tailgate_49.png`)**: 50 frames ($924 \times 502$ RGBA) showing the motorized rear hatch elevating from 0% to 100% open.
- **Sunroof / Moonroof (`scuttle_0.png` to `scuttle_49.png`)**: 50 frames ($924 \times 502$ RGBA, labeled `scuttle` in Chery's codebase) showing the glass roof panel sliding back from closed to open.
- **Steering Wheel (`steering_*.png`)**: 3 discrete state renders showing alignment.
- **Warning Alert (`icon_fault_*.png`)**: 31 animated frames for instrument/system warnings.

---

## 3. UI Implementation in `SVVehicleSetting`

In `SVVehicleSetting.apk` (`com.desaysv.vehiclesetting`):

1. **Layout (`fragment_quick_setting.xml`)**:
   - The car viewer is housed inside `container_car_model` (`FrameLayout`).
   - The rendering surface is `@id/sfv_car` (`TextureView`), laid out at full resolution ($1920 \times 720 \text{ dp}$).
   - Hotspot touch triggers (`hotArea`) are layered on top for direct interactions with doors, trunk, and windows.

2. **Surface Initialization (`QuickSettingFragment.java`)**:
   ```java
   private void initSurfaceModel() {
       this.sfvCar.setOpaque(false); // Enable transparency for UI backgrounds
       this.sfvCar.setSurfaceTextureListener(new SurfaceTextureListener() {
           public void onSurfaceTextureAvailable(SurfaceTexture st, int w, int h) {
               Surface surface = new Surface(st);
               carModelManager.surfaceCreated(surface);
           }
           public void onSurfaceTextureSizeChanged(SurfaceTexture st, int w, int h) {
               carModelManager.onSurfaceChanged(w, h);
           }
           public boolean onSurfaceTextureDestroyed(SurfaceTexture st) {
               carModelManager.onSurfaceDestroyed();
               return false;
           }
       });
       this.sfvCar.setOnTouchListener((view, motionEvent) -> {
           carModelManager.onTouchEvent(motionEvent);
           return true;
       });
   }
   ```

3. **Touch Dispatch**:
   `QuickSettingFragment` delegates raw touch events to `CarModelManager.getInstance().onTouchEvent(ev)`, which computes gesture velocity and angular displacement, updating the angle property.

---

## 4. IPC Architecture & The "Kanzi" Legacy

DeSay SV's automotive software architecture uses an abstracted IPC layer for car models:

- **AIDL Interface**: `com.desay_svautomotive.libsvcarmodel.ICarModel`
  - `c(Surface surface)` — binds the display surface.
  - `onTouchEvent(MotionEvent ev)` — forwards touch coordinates.
  - `f(String property, double val)` — sets real-valued properties (`ColorR`, `ColorG`, `ColorB`, `SkyLightOpenPercent`).
  - `h(String property, int val)` — sets integer states (`PresetAngle`, `FrontLeftWheelState`, `CircleControlState`).
- **AIDL Callback**: `com.desay_svautomotive.libsvcarmodel.ICarModelCallBack`
  - Notifies the UI when the model completes an animation, reaches a preset angle, or reports touch collision with a specific vehicle zone.

### Why "Kanzi" Appears in Smali
During static analysis of `SVLauncher` and `SVVehicleSetting`, classes such as `KanziSpriteModel` and `SpriteModelProxy` appear in smali:
- **Rightware Kanzi** is a leading 3D automotive HMI engine widely used by tier-1 suppliers.
- On premium Chery vehicle platforms (such as the Exeed TXL/VX or Tiggo 8/9 based on the **T26** platform), DeSay SV deploys an active Rightware Kanzi 3D runtime with real-time polygon rendering.
- For the budget-friendly **Tiggo 4 (T19 platform)**, DeSay SV retained the exact same `ICarModel` IPC API and `KanziSpriteModel` manager interface to minimize code divergence, but **ripped out the heavy Kanzi runtime and OpenGL 3D engine**.
- In place of the 3D engine, they substituted `FrameAnimTextureView`: a lightweight frame sequence animator that pre-loads PNGs via an LRU memory cache (`android.util.LruCache`) on a dedicated `HandlerThread`, drawing bitmaps directly to the `TextureView` surface.

---

## 5. Summary of Vehicle Resource Packages

All vehicle graphical assets are segregated into independent resource APKs in `/system/product/app/`:

| APK Name | Size | Contents & Purpose |
| :--- | :--- | :--- |
| **`MaterialVehicleT19FL.apk`** | ~163 MB | **Stock Tiggo 4 (T19FL)**: 91-frame 360° rotation (`middle_*`), 91-frame edge lighting (`edge_*`), 91-frame HVAC airflow (`blow_*`), 50-frame tailgate and sunroof sequences, seat/cabin textures. |
| **`MaterialVehicleT19FLHEV.apk`** | ~213 MB | **Tiggo 4 Hybrid (PHEV/HEV)**: Adds drivetrain energy flow sequences (`two_drive_*`, `four_drive_*`), battery charging animations, and power distribution diagrams. |
| **`MaterialVehicle.apk`** | ~284 MB | **Generic ADAS / Feature Library**: ~5,009 PNGs covering generic sedan and SUV silhouettes used for Lane Keep Assist (LKA), Blind Spot Detection (BSD), Adaptive Cruise Control (ACC), and AEB warning popups. |

---

## 6. What About the 360° Surround View Camera?

As documented in [09 — DVR & 360° Cameras](09-dvr-360-cameras.md):
- The 360° bird's-eye camera view also displays a vehicle in the center of the camera composite.
- That vehicle model is **also not rendered on the headunit**.
- The four fish-eye camera streams (front, rear, left mirror, right mirror) are stitched into a seamless bird's-eye projection **upstream inside the dedicated AVM/Camera ECU**.
- The resulting stitched composite is streamed over LVDS/AHD directly to the Qualcomm SA8155 IVI as an encoded video feed. The headunit simply renders the incoming video stream with 2D PNG icon overlays.

---

## 7. Implications for Reverse Engineering & Customization

1. **Custom Vehicle Colors / Wraps**:
   Because the car body is rendered using 8-bit grayscale + alpha frames (`middle_*_left.png`), color tinting is applied dynamically via shader / color filter using `CarModelManager.setColor(R, G, B)`. Changing the base vehicle shape, however, requires replacing all 91 PNG frames.
2. **Custom Car Model Replacements**:
   If you wish to replace the stock Tiggo 4 with a custom car model (e.g., custom rims, body kit, or a completely different vehicle), you do not need 3D modeling tools compatible with Kanzi. Instead:
   - Render 91 camera angles of your model in Blender/Maya (orthographic or standard car-viewer perspective at 4° intervals from $0^\circ \text{ to } 360^\circ$).
   - Export them as $514 \times 250$ PNGs with matching filenames (`middle_0_left.png` .. `middle_90_left.png`).
   - Repackage `MaterialVehicleT19FL.apk` and overwrite it in `/system/product/app/MaterialVehicleT19FL/`.
