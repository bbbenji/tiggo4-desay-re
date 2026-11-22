# Feature Gating & Unlocking Architecture (`CarConfigUtil` & `EolConfig`)

## 1. Architectural Overview

The Chery Tiggo 4 (2025 / T19FL) DeSay SV head unit features an enterprise automotive feature-gating architecture. All regional trims, brand themes (Chery, OMODA, JAECOO, EBRO, etc.), powertrain variants (ICE, HEV, PHEV, EV), driver assistance systems (ADAS), and smartphone projection interfaces (Wireless Apple CarPlay, Android Auto, CarLink) are compiled directly into the stock system images and gated purely at runtime via a centralized configuration bus.

```
                           +-------------------------------------+
                           |      Hardware eMMC Storage          |
                           |    /dev/block/by-name/eolcfg        |
                           +------------------+------------------+
                                              |
                                              v  SVPPersistStorage_EMMC
                           +-------------------------------------+
                           |    VehicleDevice HAL Daemon         |
                           |  com.desaysv.vehicledevice@1.0      |
                           |  (libdesaysv_vehicledevice.so)      |
                           +------------------+------------------+
                                              |
                                              v  VDBus IPC Broker
                     +------------------------+------------------------+
                     |                                                 |
             Event 0xE0006                                     Event 0xE0579
       (PROJECT_RESERVE_CONFIGS)                    (PROJECT_VEHICLE_PROPERTY_CONFIG_UPDATE)
                     |                                                 |
                     v                                                 v
        +-------------------------+                       +-------------------------+
        |   CarConfigUtil /       |                       |   Dynamic App Reload    |
        |   EolConfig             |                       |   (SVLauncher,          |
        |   (Java Framework API)  |                       |    SVVehicleSetting)    |
        +------------+------------+                       +-------------------------+
                     |
       +-------------+-------------+
       |                           |
       v                           v
Direct Properties           EOL Bitfields (210 IDs)
(CarPlay, AA, Wi-Fi)       (ADAS, Brand, Powertrain, Comfort)
```

### Component Hierarchy

1. **Hardware Persistence Partition (`/dev/block/by-name/eolcfg`)**:
   - Raw eMMC partition managed by `libproperties_vehicle.so` via `SVPPersistStorage_EMMC` and `SVPSimplePropertyVector`.
   - Uses fixed-record key-value mapping (`SVPSimplePropertyVector(buffer, size, key_len, val_len)`).
   - Thread-safe access is guarded across processes via `/dev/run/.vehicle.persist.locked`.

2. **VehicleDevice HAL Daemon (`com.desaysv.vehicledevice@1.0-service`)**:
   - Runs as `root:system` under `/vendor/bin/hw/com.desaysv.vehicledevice@1.0-service`.
   - Houses `VehicleConfigStore` which bridges hardware flash storage with Android system properties (`property_get` / `property_set`) and the DeSay Vehicle Bus (`VDBus`).

3. **VDBus IPC Broker (`com.desaysv.ivi.vdb`)**:
   - Custom low-latency IPC bus connecting native daemons and Java system applications.
   - **`0xE0006` (`PROJECT_RESERVE_CONFIGS`)**: Synchronous query event used by apps to retrieve configuration properties.
   - **`0xE0579` (`PROJECT_VEHICLE_PROPERTY_CONFIG_UPDATE`)**: Broadcast notification triggered whenever vehicle properties are altered.

4. **Client Framework (`CarConfigUtil` & `EolConfig`)**:
   - Included in all system apps (`SVLauncher`, `SVVehicleSetting`, `SVSystemUI`, `MaterialVehicleT19FL`).
   - Package: `com.desaysv.ivi.extra.project.carconfig`.
   - Connects to `VDBus`, registers for `0xE0579` updates, and exposes typed helper methods (`isCarplayCertificated()`, `isAACertificated()`, `getConfig(id)`).

---

## 2. Phone Link Gating (Wireless CarPlay & Android Auto)

### Direct Persistence Properties

Smartphone projection availability is evaluated via direct boolean property strings in `CarConfigUtil.smali`:

| Method | Property Checked | Fallback Property | Description |
|---|---|---|---|
| `isCarplayCertificated()` | `vehicle.persist.carplay.certification.enabled` | `persist.sys.carplay.certification` | Apple CarPlay UI shortcut and connection daemon |
| `isAACertificated()` | `vehicle.persist.androidauto.certification.enabled` | `persist.sys.androidauto.certification` | Google Android Auto UI shortcut and daemon |
| `isWiFiCertificated()` | `vehicle.persist.wifi.certification.enabled` | `persist.sys.wifi.certification` | 5GHz Wi-Fi SoftAP required for wireless handoff |
| `isCarLinkWirelessByProperties()` | `vehicle.persist.sys.carlink.wireless.enabled` | - | Wireless CarLink phone mirroring |
| `isCarLinkWiredByProperties()` | `vehicle.persist.sys.carlink.wired.enabled` | - | Wired CarLink phone mirroring |
| `isCarlifeCertificated()` | `vehicle.persist.carlife.certification.enabled` | `persist.sys.carlife.certification` | Baidu CarLife projection |
| `isHicarCertificated()` | `vehicle.persist.hicar.certification.enabled` | `persist.sys.hicar.certification` | Huawei HiCar projection |

### Underlying Binary Stack Verification

Inspection of `/etc_product/` confirms that the head unit firmware is already fully configured for **both wired and wireless** operation without hardware restrictions:

- **Apple CarPlay SDK (`/etc_product/vehicle.carplaySDK.conf`)**:
  ```ini
  [USE_FOR_AUTHENTICATION]
  CONF_AUTHENTICATION = false       # Hardware Apple MFi authentication chip check disabled!

  [USE_FOR_WIRED_ONLY]
  CONF_WIRED_ONLY = true

  [USE_FOR_WIRELESS_ONLY]
  CONF_WIRELESS_ONLY = true         # Both wired and wireless CarPlay explicitly enabled!

  [USE_FOR_WIRELESS_AUDIO_FORMAT]
  CONF_WIRELESS_AUDIO_FORMAT = AAC-ELD
  ```

- **Android Auto Service (`/etc_product/vehicle.androidauto.conf`)**:
  ```ini
  CONF_WIFI_CHANNELS = 5745,5765
  CONF_AP_CHANNEL = 161             # 5GHz UNII-3 Wi-Fi channel for low-latency video streaming
  CONF_WIFI_INTERFACE = wlan0
  CONF_GPS_DATA = true              # Shares vehicle GNSS coordinates with mobile phone
  ```

- **USB OTG Network Gadget (`/etc_vendor/vehicle.phonelink.conf`)**:
  ```xml
  <config>
      <UeventPath value="/sys/devices/platform/soc/a600000.ssusb"/>
      <UsbModePath value="/sys/devices/platform/soc/a600000.ssusb/mode"/>
      <RoleUSBtoHostMode value="echo host > "/>
      <RoleUSBtoDeviceMode value="echo peripheral > "/>
      <CarplayNetPath value="/devices/platform/soc/a600000.ssusb/a600000.dwc3/gadget/net/carplay0" />
  </config>
  ```

When `vehicle.persist.carplay.certification.enabled` and `vehicle.persist.androidauto.certification.enabled` are set to `"true"`, `SVLauncher` dynamically activates the projection cards on the primary home screen and enables Bluetooth pairing handoff to the 5GHz Wi-Fi AP (`channel 161`).

---

## 3. EOL Configuration Bitfield Architecture (`EolConfig`)

Configurations requiring multi-state or bitfield settings are organized into eight 16-character hex string properties representing 8-byte arrays:

- `vehicle.persist.project.ext.configs` (`mCarConfig1`)
- `vehicle.persist.project.ext.configs2` (`mCarConfig2`)
- `vehicle.persist.project.ext.configs3` (`mCarConfig3`)
- `vehicle.persist.project.ext.configs4` (`mCarConfig4`)
- `vehicle.persist.project.ext.configs5` (`mCarConfig5`)
- `vehicle.persist.project.ext.configs6` (`mCarConfig6`)
- `vehicle.persist.project.ext.configs7` (`mCarConfig7`)
- `vehicle.persist.combo.config` (`mComboConfig`)

Each hex string is converted to an integer byte array by `Utils.stringToByte(hex)`. `EolConfig.getCheryEolConfig(int id)` evaluates 210 distinct configuration IDs (`0x00` through `0xD1`).

### Critical Config ID Mapping

#### 1. Vehicle Platform, Powertrain, and Identity

| Config ID | Hex ID | Storage Array | Byte Index | Bit Shift | Bit Mask | Decoded Meaning |
|---|---|---|---|---|---|---|
| **Powertrain** | `0x9A` | `mCarConfig5` | Byte 4 | `>> 4` | `0x03` | `0` = ICE, `1` = PHEV, `2` = EV, `3` = HEV |
| **Platform** | `0x9B` | `mCarConfig5` | Byte 4 | `>> 6` | `0x01` | `0` = T19FL (Tiggo 4 FL), `1` = T26 |
| **Model Code** | `0xA0` | `mComboConfig` | Byte 1 | `>> 0` | `0xFF` | `0x0C` = T19FL, `0x07` = T19C, `0x01` = T26, `0x09` = T1EJ |
| **Brand Theme** | `0xB7` | `mComboConfig` | Byte 6 | `>> 0` | `0xFF` | `1`=Chery, `2`=Chirey, `4`=OMODA, `6`=JAECOO, `8`=EBRO |

#### 2. Advanced Driver Assistance Systems (ADAS)

In `DriveAssistantSettingFragment.smali`, configuration ID `0x70` functions as a **Master Override**: setting `0x70 == 1` automatically renders and activates all 13 ADAS functional blocks in the HMI.

| Config ID | Hex ID | Storage Array | Byte Index | Bit Shift | Bit Mask | Feature Description |
|---|---|---|---|---|---|---|
| **Master ADAS** | `0x70` | `mCarConfig4` | Byte 3 | `>> 5` | `0x01` | **Master ADAS Suite** (Enables all 13 systems) |
| **FCW** | `0x54` | `mCarConfig4` | Byte 2 | `>> 0` | `0x01` | Forward Collision Warning |
| **AEB** | `0x1B` | `mCarConfig4` | Byte 1 | `>> 0` | `0x01` | Autonomous Emergency Braking |
| **LDW** | `0x14` | `mCarConfig4` | Byte 0 | `>> 0` | `0x01` | Lane Departure Warning |
| **LKA / LDP** | `0x4F` | `mCarConfig4` | Byte 0 | `>> 4` | `0x01` | Lane Keeping Assist / Intervention |
| **ELK** | `0x4E` | `mCarConfig4` | Byte 0 | `>> 2` | `0x01` | Emergency Lane Keeping |
| **BSD** | `0x49` | `mCarConfig3` | Byte 7 | `>> 4` | `0x01` | Blind Spot Detection |
| **RCTA** | `0x57` | `mCarConfig4` | Byte 2 | `>> 6` | `0x01` | Rear Cross Traffic Alert |
| **RCW** | `0x80` | `mCarConfig5` | Byte 0 | `>> 0` | `0x01` | Rear Collision Warning |
| **DOW** | `0x2E` | `mCarConfig3` | Byte 7 | `>> 6` | `0x01` | Door Opening Warning |
| **TSR / SLA** | `0x68` | `mCarConfig4` | Byte 3 | `>> 0` | `0x01` | Traffic Sign Recognition / Speed Limit Assist |
| **ACC / Speed**| `0x4D` | `mCarConfig3` | Byte 6 | `>> 0` | `0x01` | Intelligent Adaptive Cruise Control |
| **IHC** | `0x88` | `mCarConfig5` | Byte 2 | `>> 0` | `0x01` | Intelligent High Beam Assist |
| **CST** | `0x8E` | `mCarConfig5` | Byte 3 | `>> 0` | `0x01` | Comfort Stop (Brake Pitch Control) |

#### 3. Vehicle Lighting, Comfort, & Hardware Equipment

| Config ID | Hex ID | Storage Array | Byte Index | Bit Shift | Bit Mask | Feature Description |
|---|---|---|---|---|---|---|
| **Ambient Presence** | `0x1E` | `mCarConfig6` | Byte 2 | `>> 7` | `0x01` | Interior Atmosphere Lighting Present |
| **Dynamic Rhythm** | `0x6D` | `mCarConfig4` | Byte 4 | `>> 0` | `0x01` | Multi-Color Drive Mode Light Rhythm |
| **Music Rhythm** | `0x75` | `mCarConfig4` | Byte 2 | `>> 6` | `0x01` | Ambient Light Music Rhythm Sync |
| **Sunroof** | `0x3B` | `mCarConfig4` | Byte 1 | `>> 4` | `0x01` | Panoramic Glass / Sunroof Controls |
| **Power Tailgate** | `0x15` | `mCarConfig4` | Byte 0 | `>> 2` | `0x01` | Power Liftgate (PLG) Screen Toggle |
| **Tailgate Type** | `0xA4` | `mCarConfig5` | Byte 7 | `>> 2` | `0x03` | `0` = Manual, `1` = Electric, `2` = Foot Sensor |
| **Wireless Charger** | `0x89` | `mCarConfig5` | Byte 2 | `>> 2` | `0x01` | Qi Wireless Charging Pad Toggle |
| **Seat Memory** | `0x7F` | `mCarConfig4` | Byte 6 | `>> 0` | `0x01` | Driver Electric Seat Memory & Welcome |
| **Seat Heat/Vent** | `0x22` | `mCarConfig6` | Byte 3 | `>> 0` | `0x01` | Seat Heating and Ventilation |
| **Seat Massage** | `0x45` | `mCarConfig3` | Byte 6 | `>> 2` | `0x01` | Driver & Passenger Seat Massage |
| **Smart Key PEPS** | `0x28` | `mCarConfig6` | Byte 4 | `>> 0` | `0x01` | Passive Entry Passive Start Settings |
| **Auto Roll-Up** | `0x2A` | `mCarConfig6` | Byte 4 | `>> 4` | `0x01` | Walk-Away Automatic Window Roll-Up |
| **W-HUD** | `0x6A` | `mCarConfig4` | Byte 3 | `>> 2` | `0x01` | Windshield Head-Up Display |
| **AR-HUD** | `0x6C` | `mCarConfig4` | Byte 3 | `>> 4` | `0x01` | Augmented Reality HUD |

---

## 4. Multi-Brand HMI Theming (`0xB7`)

The head unit firmware is shared across Chery's global brands. Setting byte 6 of `vehicle.persist.combo.config` changes the brand theme across all apps:

| Brand Code | Brand Name | Target Markets | HMI Changes |
|---|---|---|---|
| `1` | **Chery** | Global / Domestic | Standard Chery logo, red/silver boot animation |
| `2` | **Mexico Chery (Chirey)** | Mexico & Central America | "CHIREY" branding, Spanish language defaults |
| `3` | **Italian Chery** | Southern Europe | European acoustic chime profile |
| `4` | **OMODA** | Global (Europe, SA, Oceania) | Futuristic neon theme, OMODA vehicle silhouette |
| `5` | **Central Asia** | CIS / Central Asia | Russian language & Cyrillic keyboard defaults |
| `6` | **JAECOO** | Global (Europe, SA, SEA) | Rugged luxury theme, JAECOO badge, green/bronze accents |
| `7` | **Lucano** | Select European Markets | Regional styling variant |
| `8` | **EBRO** | Spain (Barcelona Plant) | EBRO emblem, Spanish factory badge |
| `9` | **Tenet** | Regional Sub-brand | Fleet / Commercial styling |
| `10` | **Onives** | Regional Sub-brand | Custom UI asset set |
| `12` | **Esteo** | Regional Sub-brand | Custom UI asset set |

---

## 5. Feature Unlocking Instructions

### Method A: Automated CLI Tool (`chery_carconfig_tool.py`)

A standalone decoder and craft tool is available in [scripts/chery_carconfig_tool.py](file:///home/benji/git/tiggo4-desay-re/scripts/chery_carconfig_tool.py).

#### 1. Inspect Current Vehicle Configuration
```bash
python3 scripts/chery_carconfig_tool.py decode \
    --configs4 0000000000000000 \
    --configs5 0014818239003600 \
    --configs6 0000000000000000 \
    --combo 000C000000000100
```

#### 2. Generate Unlocking Payload & Shell Script
To unlock Wireless CarPlay, Android Auto, the full ADAS suite, and ambient lighting under the OMODA theme:
```bash
python3 scripts/chery_carconfig_tool.py unlock \
    --features carplay androidauto adas ambient \
    --set-brand omoda \
    -o unlock.sh
```

### Method B: Manual ADB Root Shell Commands

Connect to the head unit via ADB over Wi-Fi or USB OTG (see [docs/02-adb-access.md](file:///home/benji/git/tiggo4-desay-re/docs/02-adb-access.md)) and execute:

```bash
# 1. Enable Wireless CarPlay & Android Auto
setprop vehicle.persist.carplay.certification.enabled true
setprop persist.sys.carplay.certification true
setprop vehicle.persist.androidauto.certification.enabled true
setprop persist.sys.androidauto.certification true
setprop vehicle.persist.wifi.certification.enabled true

# 2. Enable Full ADAS Suite & Dynamic Ambient Lighting
# Sets configs4[3] bit 5 (0x20 = Master ADAS) and configs4[4] bit 0 (0x01 = Dynamic Rhythm)
# Sets configs4[2] bit 6 (0x40 = Music Rhythm Sync)
setprop vehicle.persist.project.ext.configs4 0000402001000000

# Sets configs6[2] bit 7 (0x80 = Ambient Light Present)
setprop vehicle.persist.project.ext.configs6 0000800000000000

# 3. Optional: Switch Brand Theme to OMODA (0x04) or JAECOO (0x06)
# combo.config: byte 1 = Model (0x0C = T19FL), byte 6 = Brand (0x04 = OMODA)
setprop vehicle.persist.combo.config 000C000000000400

# 4. Trigger Dynamic App Reload
# Broadcast VDBus notification to force active daemons to read modified properties
am broadcast -a com.desaysv.ivi.vdb.ACTION_CONFIG_UPDATE
pkill -f com.desaysv.launcher
pkill -f com.desaysv.vehiclesetting
```

---

## 6. Verification and Troubleshooting

1. **Verify Property Registration**:
   ```bash
   getprop vehicle.persist.carplay.certification.enabled
   # Expected: true
   getprop vehicle.persist.androidauto.certification.enabled
   # Expected: true
   ```

2. **Verify VDBus Log Output**:
   Monitor logcat while restarting the launcher:
   ```bash
   logcat -s VDB-EXTRA-CarConfigUtil VDB-EXTRA-EolConfig
   ```
   You should see:
   ```
   D VDB-EXTRA-EolConfig: [loadConfig] CarConfig4:0000402001000000
   D VDB-EXTRA-EolConfig: [loadConfig] mComboConfig:000C000000000400
   I VDB-EXTRA-CarConfigUtil: isCarplayCertificated -> true
   I VDB-EXTRA-CarConfigUtil: isAACertificated -> true
   ```

3. **Persistent Hardware Commit**:
   To ensure values survive a cold 12V battery pull, the `VehicleConfigStore` automatically syncs Android properties back to the physical `/dev/block/by-name/eolcfg` partition during normal graceful vehicle power-down (ACC off $\to$ STR standby).
