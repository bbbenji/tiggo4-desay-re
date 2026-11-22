# 03 — The MCU Wire Protocol (`192.168.8.1:10004`)

## Overview

The Qualcomm SA8155 IVI communicates with the vehicle Microcontroller Unit (MCU) and body-control network over an internal dedicated TCP socket at **`192.168.8.1:10004`**.

This path operates as the direct hardware communication channel between the Android vehicle HAL layer and the vehicle MCU. Reverse-engineering of `vendor/lib64/hw/remote.spi2ethenet.default.so` and `vendor/lib64/librmtadapter_vehicle.so` reveals the complete wire framing, session lifecycle, and signal encoding used by the Chery Tiggo 4 / DeSay platform.

---

## 1. Architecture & Transport Stack

```
┌────────────────────────────────────────────────────────┐
│     Android Applications / System Settings / UI         │
│     (SVVehicleSetting, SVEngMode, CarStateService)     │
└──────────────────────────┬─────────────────────────────┘
                           │ AIDL IPC (VDEvent: 0x7523 / 0x7353)
┌──────────────────────────▼─────────────────────────────┐
│             desaysv_vdb_emulator (PID 507)             │
│        (libdesaysv_vehiclebus_backend_aidl.so)         │
└──────────────────────────┬─────────────────────────────┘
                           │ SVPRmtAdapter::SendCommand()
┌──────────────────────────▼─────────────────────────────┐
│              librmtadapter_vehicle.so                  │
│       Encodes opcode & signals into frame payload      │
└──────────────────────────┬─────────────────────────────┘
                           │ svp_pipe_start / send_message()
┌──────────────────────────▼─────────────────────────────┐
│          remote.spi2ethenet.default.so                 │
│   Wraps in {{ ... }} framing, manages TCP socket        │
└──────────────────────────┬─────────────────────────────┘
                           │ TCP Socket (AF_INET, Port 10004)
                           │ Also /dev/socket/spi_test_fifo
┌──────────────────────────▼─────────────────────────────┐
│                 Vehicle MCU (192.168.8.1)              │
│       CAN Gateway, Power, HVAC, Body Electronics       │
└────────────────────────────────────────────────────────┘
```

- **Endpoint:** `192.168.8.1:10004` (TCP)
- **Local Diagnostic Pipe:** `/dev/socket/spi_test_fifo` (UNIX FIFO created via `mkfifo`)
- **Native HAL Driver:** `remote.spi2ethenet.default.so`
- **Adapter Library:** `librmtadapter_vehicle.so`
- **Socket Options:** `TCP_NODELAY = 1`, `MSG_NOSIGNAL`

---

## 2. On-Wire Frame Structure

All frames exchanged across `192.168.8.1:10004` are delimited by double ASCII curly braces (`{{` and `}}`):

```
+-------+-----------+---------+---------+-----------+-----------+-------------+-------+
|  0..1 |     2     |    3    |    4    |     5     |     6     |   7..N-3    | N-2..N|
+-------+-----------+---------+---------+-----------+-----------+-------------+-------+
| 7B 7B | Subsystem | Cmd MSB | Cmd LSB | Len (LSB) | Len (MSB) |   Payload   | 7D 7D |
|  {{   | (cmd>>8)&7|         |         | (data+1)  |   0x00    | (Param+Val) |  }}   |
+-------+-----------+---------+---------+-----------+-----------+-------------+-------+
```

### Frame Fields
1. **Preamble (2 bytes):** `0x7B 0x7B` (ASCII `{{`)
2. **Subsystem Routing Index (1 byte):** Calculated by `SVPRmtAdapter::SendCommand` as `(cmd >> 8) & 0x07`.
   - `0x01`: Chassis / Vehicle Dynamics (wheel speeds, steering angle, odometer)
   - `0x02`: System Power & Heartbeat
   - `0x03`: Body, Battery & Charging Telemetry
   - `0x04`: Audio / Tuner / Keypad Link
   - `0x05`: Cabin Comfort & Controls (HVAC, Ambient Lighting, Settings)
   - `0x06`: High-rate Sensor Stream
3. **Command / Opcode ID (2 bytes, Big-Endian):** `(cmd >> 8) & 0xFF`, `cmd & 0xFF`.
4. **Length Field (2 bytes, Little-Endian):** Payload length + 1 (i.e. length of parameter ID and value bytes).
5. **Payload ($N$ bytes):**
   - **Signal / Parameter ID (2 bytes, Big-Endian):** Identifies the specific vehicle property (e.g. `0xFE4C` for 12V voltage, `0x1042` for HVAC temp).
   - **Data Length (1 byte):** Length of following data bytes.
   - **Value ($M$ bytes):** Raw sensor telemetry or commanded setting.
6. **Postamble (2 bytes):** `0x7D 0x7D` (ASCII `}}`)

---

## 3. Session Handshake & Keepalive Protocol

The HAL driver enforces strict session initialization and link monitoring in native background threads:

### Initial Handshake
Immediately upon establishing the TCP socket to `192.168.8.1:10004`, `socket_connect_thread_proc` transmits a connection initialization frame:
```hex
7B 7B 05 55 01 02 00 01 7D 7D
```
- **Opcode:** `0x5501`
- **Payload:** `0x01` (HAL Ready)
- **Logcat confirmation:** `socket_connect_thread_proc socket 0x5501 finished!`

### Periodic Keepalive (Heartbeat)
A dedicated background thread (`ping_thread_proc`) transmits a heartbeat frame every **2 seconds**:
```hex
7B 7B 02 02 05 03 00 00 02 7D 7D
```
- **Subsystem:** `0x02`
- **Opcode:** `0x0205`
- **Data:** `0x00 0x02`

### MCU Heartbeat Acknowledgment
The MCU responds with opcode `0x511C`:
```hex
7B 7B 01 51 1C 02 00 02 7D 7D
```
- When `remote.spi2ethenet.default.so` receives `0x511C 02 00 02`, it zeroes the internal missing heartbeat counter (`ldr x8, [x8, #0x218]; str wzr, [x8]`).
- If **15 consecutive heartbeat queries** elapse without receiving an ACK from the MCU, the driver logs:
  `already sent 0x0205 15 times but not received ack. reconnect!`
  and terminates/reconnects the TCP socket.
- If the socket fails repeatedly, it logs:
  `ERROR!!! socket_connect_thread_proc socket connect 50 times failures!!`
  and executes `netstat -n >> /data/local/netstat.txt` via `system()` to dump connection diagnostics.

---

## 4. Decoded Commands & Telemetry Dictionary

### VDBus Event `0x7523` (`SV_CAN_LINK_CMD`) — Host to MCU
Dispatched when Android apps invoke `sendVDBEvent(0x7523, "IntVector", data)`.

| Parameter ID | Name | Data Format | Description / Scale |
|:---:|:---|:---:|:---|
| `0xFFFE 014C` | Query 12V Battery | 0 bytes | Triggers MCU to broadcast 12V voltage telemetry (`0xFE4C`) |
| `0xFFFE 0160` | Query Battery SOC | 0 bytes | Triggers MCU to broadcast High-Voltage SOC percentage |
| `0xFFFE 0161` | Query Battery SOH | 0 bytes | Triggers MCU to broadcast High-Voltage SOH percentage |
| `0xFFFE 010D` | Query Battery State | 0 bytes | Triggers battery operational status query |
| `0xFF10 0151` | Query AutoShow Mode| 0 bytes | Queries Showroom / AutoShow mode switch |
| `0x1051` | Set AutoShow Mode | `uint8` | `0x00` = Disabled, `0x01` = Showroom Mode Enabled |
| `0x1042` | Driver Target Temp | `uint8` | Temperature setpoint ($16^\circ\text{C}$ to $32^\circ\text{C}$) |
| `0x1043` | Passenger Target Temp| `uint8` | Temperature setpoint |
| `0x1044` | Blower Speed | `uint8` | Fan speed level ($0$ to $7$) |
| `0x1045` | Blower Direction | `uint8` | Air distribution (Face / Foot / Defrost) |
| `0x1046` | AC Mode | `uint8` | `0x00` = Off, `0x01` = AC On, `0x02` = AC Max |
| `0x1047` | Recirculation Mode | `uint8` | `0x01` = Fresh Air, `0x02` = Recirculation, `0x03` = Auto |
| `0x1048` | Dual Temp Sync | `uint8` | `0x00` = Independent, `0x01` = Dual Synced |
| `0x1049` | Front Defrost | `uint8` | `0x00` = Off, `0x01` = Max Defrost |
| `0x104A` | Rear Defrost / Mirror | `uint8` | `0x00` = Off, `0x01` = Heating Active |
| `0x104B` | Auto HVAC Mode | `uint8` | `0x00` = Manual, `0x0C` = Auto Active |
| `0x104C` | Seat Ventilation/Heat| `uint16`| Driver & passenger heating/cooling levels |
| `0x104E` | Cabin Air Quality | `uint8` | PM2.5 / Ionizer filter state |
| `0x0A58` | Steering Wheel Heat | `uint8` | `0x00` = Off, `0x01`..`0x04` = Heat levels |

---

### VDBus Event `0x7353` (`SV_CAN_LINK_DATA`) — MCU to Host
Broadcast from the MCU whenever sensor values update or in response to queries.

| Parameter ID | Name | Length | Decoded Formula / Meaning |
|:---:|:---|:---:|:---|
| `0xFE4C` | 12V Battery Voltage | 2 bytes | Value is in units of 0.1V: `(data[0] << 8 | data[1]) / 10.0` V (e.g. `0x007A` = 122 = **12.2V**) |
| `0xFE60` | HV Battery SOC | 1 byte | Percentage value: `data[0]` % |
| `0xFE61` | HV Battery SOH | 1 byte | Percentage value: `data[0]` % |
| `0xFE6D` | Traction DCDC State | 4 bytes | High-voltage traction battery & DC-DC converter operating status |
| `0xFE77` | Charging / OBC Status | 2 bytes | On-Board Charger plug connection and charging state |
| `0xFE1C` | Body Door / Latch State| 4 bytes | Status of 4 doors, trunk, and hood latches |
| `0xFE1D` | Window Positions | 4 bytes | Front/rear power window positions ($0$ to $100\%$) |
| `0xFE1E` | Sunroof Position | 2 bytes | Tilt/slide position ($0$ to $100\%$) |
| `0xFE1F` | Tire Pressure / TPMS | 8 bytes | Individual wheel pressure and temperature alarms |

---

### Opcode `0x3D01` — Ambient Lighting Control
Dispatched on Subsystem `0x05`:
```hex
7B 7B 05 3D 01 08 00 16 09 35 1A 0A 16 01 7D 7D
```
- **Opcode:** `0x3D01`
- **Length:** `0x08 0x00` (7 payload bytes + 1)
- **Payload format:** `[Zone, Red, Green, Blue, Brightness, Rhythm_Mode, Switch]`

---

### Opcode `0x40FF` — High-Rate Vehicle Dynamics
Streamed on Subsystem `0x01` (up to 20Hz):
```hex
7B 7B 01 40 FF 22 00 A1 FF FF ... 7D 7D
```
- **Opcode:** `0x40FF`
- **Length:** `0x22` (34 bytes)
- **Payload contents:** Four-wheel speed sensors, steering wheel angle sensor ($0.1^\circ$ resolution), yaw rate, gear position (`P/R/N/D`), and total vehicle odometer.

---

## 5. Packet Dissection & Analysis Tool

The repository provides a dedicated command-line dissector and frame generator in `scripts/chery_mcu_tool.py`:

```bash
# Dissect a raw hex frame or logcat snippet
python3 scripts/chery_mcu_tool.py dissect "7B 7B 03 73 53 06 00 FE 4C 02 00 7A 7D 7D"

# Dissect an unadorned Logcat line
python3 scripts/chery_mcu_tool.py dissect "Write: 05 75 23 05 00 10 42 01 19"

# Generate an encoded wire frame for a command
python3 scripts/chery_mcu_tool.py craft --cmd 0x7523 --param 0xFE4C --subsystem 5

# Parse live packet stream from a logcat file
python3 scripts/chery_mcu_tool.py analyze-logcat --file /path/to/logcat.txt
```

---

## 6. Behavior & Operational Constraints

1. **State Ownership & Periodic Synchronization:**
   The Android platform's `VehicleService` daemon continuously synchronizes vehicle settings with the MCU. Transient modifications sent to the socket from external clients will be overwritten on the next periodic state update cycle.
2. **Framing Sensitivity:**
   The MCU parser requires strict `{{` and `}}` framing. Sending malformed packets or raw unframed bytes desynchronizes the SPI/Ethernet bridge inside the MCU firmware, causing the body-control communications bus to lock up until a full vehicle ignition cycle or 12V battery reset is performed.
