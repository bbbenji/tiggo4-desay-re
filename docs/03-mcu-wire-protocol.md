# 03 — The MCU Wire Protocol (`192.168.8.1:10004`)

## Overview

The Qualcomm SA8155 IVI communicates with the vehicle Microcontroller Unit (MCU) and body-control network over an internal dedicated TCP socket at **`192.168.8.1:10004`**.

This path operates as a direct control channel to the MCU. Understanding this wire protocol is essential for analyzing how low-level vehicle commands (such as ambient lighting, seat controls, and body hardware) are dispatched from Android down to the physical vehicle electronics.

---

## 1. Transport & Wire Specifications

- **Endpoint:** `192.168.8.1:10004` (TCP)
- **Socket Parameters:** `socket(AF_INET, SOCK_STREAM | NONBLOCK | CLOEXEC)`
- **Behavior:** Connects and performs `sendto(..., MSG_NOSIGNAL)` with a ~1-second timeout enforced via `poll`.
- **Channel Characteristics:** The socket is primarily a **write-only command channel**; telemetry events are broadcast back over a persistent platform session or via the VDBus service architecture.

---

## 2. Frame Structure

Commands dispatched to port 10004 use framed packets demarcated by double curly braces (`{{` and `}}` in ASCII):

```
7B 7B | (cmd>>8)&7 | (cmd>>8)&0xFF | cmd&0xFF | (len+1) | 00 | payload[len] | 7D 7D
└─{{─┘   └──────────────── header ───────────────────┘          └─ payload ──┘   └─}}─┘
```

- **Preamble:** `0x7B 0x7B` (`{{`)
- **Header:** 3 bytes containing the command ID:
  - Byte 0: `(cmd >> 8) & 0x07`
  - Byte 1: `(cmd >> 8) & 0xFF`
  - Byte 2: `cmd & 0xFF`
- **Length Field:** `(len + 1)` followed by `0x00`
- **Payload:** Raw parameter bytes ($\le 250$ bytes)
- **Postamble:** `0x7D 0x7D` (`}}`)

### Concrete Example: Ambient Light Color Set (`0x3D01`)
For ambient lighting opcode `0x3D01` with a 7-byte color payload `16 09 35 1A 0A 16 01` (red preset), the on-wire byte stream is:

```hex
7B 7B 05 3D 01 08 00 16 09 35 1A 0A 16 01 7D 7D
```

- `7B 7B` = Start frame delimiter `{{`
- `05 3D 01` = Opcode header for `0x3D01`
- `08 00` = Payload length ($7 + 1 = 8$)
- `16 09 35 1A 0A 16 01` = Payload bytes
- `7D 7D` = End frame delimiter `}}`

---

## 3. Relationship to DeSay CabinLan & VDBus

The headunit features two distinct hardware bridges:

1. **The MCU Socket (`192.168.8.1:10004`)**:
   Direct TCP socket to the vehicle microcontroller. Wraps property opcodes into the framed `{{ ... }}` structure.
2. **The DeSay "CabinLan" Bridge (`192.168.225.1:16000`)**:
   Runs SOME/IP over TCP/UDP (`com.desaysv.libsomeiputil`) to communicate across the high-speed Ethernet backbone between the Android IVI and the QNX instrument cluster / gateway ECU.

These operate as parallel, non-interfering bridges on completely different IP subnets.

---

## 4. Behavior & Safety Constraints

### Handling from Unprivileged Apps
The TCP socket at `192.168.8.1:10004` is network-accessible from standard Android apps (`untrusted_app`). However, sending one-shot frames from an unprivileged client produces **no vehicle state change** on a production vehicle:
- The platform's native service daemon maintains a persistent state session with the MCU.
- Any transient state modifications sent over ad-hoc socket connections are immediately overwritten by the periodic state synchronization loop maintained by `SVVehicleSetting` / VDBus.

### Bus Desynchronization Warning
> [!CAUTION]
> **Do not send malformed, truncated, or unframed packets to `192.168.8.1:10004`.**
> Fuzzing or sending invalid data frames to this port has been observed to cause an SPI/Ethernet framing desynchronization inside the MCU, locking the vehicle body bus and requiring a **complete vehicle power cycle** (disconnecting the 12V battery or letting the vehicle enter deep sleep) to recover.
