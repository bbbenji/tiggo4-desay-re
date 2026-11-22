#!/usr/bin/env python3
"""
Chery Tiggo 4 / DeSay IVI MCU Wire Protocol Dissector & Packet Crafting Tool
Analyzes and encodes framed communication packets for the internal TCP socket (192.168.8.1:10004).

Based on reverse engineering of:
- vendor/lib64/hw/remote.spi2ethenet.default.so
- vendor/lib64/librmtadapter_vehicle.so
- com.desaysv.ivi.vdb AIDL bindings (SV_CAN_LINK_CMD 0x7523 / SV_CAN_LINK_DATA 0x7353)
"""

import argparse
import binascii
import re
import sys
from typing import Dict, List, Optional, Tuple

# Subsystem Mapping ((cmd >> 8) & 0x07)
SUBSYSTEM_NAMES: Dict[int, str] = {
    0x01: "Chassis / Vehicle Dynamics",
    0x02: "System Power & Heartbeat",
    0x03: "Body & Battery Telemetry",
    0x04: "Audio / Tuner / Keypad Link",
    0x05: "Cabin Comfort & Controls",
    0x06: "High-Rate Sensor Stream",
}

# Opcode Mapping
OPCODE_NAMES: Dict[int, str] = {
    0x0205: "HEARTBEAT_PING",
    0x511C: "HEARTBEAT_ACK",
    0x5501: "CONNECT_INIT",
    0x3D01: "AMBIENT_LIGHT_SET",
    0x40FF: "CHASSIS_DYNAMICS_STREAM",
    0x4203: "POWER_MANAGEMENT_CMD",
    0x4204: "POWER_MANAGEMENT_STATUS",
    0x4401: "KEYPAD_EVENT",
    0x7353: "SV_CAN_LINK_DATA",
    0x7523: "SV_CAN_LINK_CMD",
    0x7905: "VEHICLE_SPEED_STATUS",
}

# Parameter / Signal ID Mapping
PARAM_NAMES: Dict[int, Tuple[str, str]] = {
    # 0x7523 Commands
    0x1042: ("HVAC_DRIVER_TEMP", "Driver target temperature setpoint"),
    0x1043: ("HVAC_PASSENGER_TEMP", "Passenger target temperature setpoint"),
    0x1044: ("HVAC_BLOWER_SPEED", "Blower fan speed level (0-7)"),
    0x1045: ("HVAC_AIR_DISTRIBUTION", "Air distribution mode (Face/Foot/Defrost)"),
    0x1046: ("HVAC_AC_MODE", "A/C compressor state (0=Off, 1=On, 2=Max)"),
    0x1047: ("HVAC_RECIRCULATION", "Air intake (1=Fresh, 2=Recirc, 3=Auto)"),
    0x1048: ("HVAC_DUAL_SYNC", "Dual-zone temperature synchronization"),
    0x1049: ("HVAC_FRONT_DEFROST", "Front windshield defrost"),
    0x104A: ("HVAC_REAR_DEFROST", "Rear window / side mirror defogger"),
    0x104B: ("HVAC_AUTO_MODE", "Automatic climate control mode"),
    0x104C: ("HVAC_SEAT_HEATING", "Driver/passenger seat heating & ventilation"),
    0x104D: ("HVAC_OFF_SWITCH", "Master HVAC power switch"),
    0x104E: ("HVAC_AIR_QUALITY", "PM2.5 / Air ionizer / filtration system"),
    0x0A55: ("CABIN_VENTILATION", "Cabin ventilation setting"),
    0x0A57: ("STEERING_HEAT_TIMER", "Steering wheel heating timer"),
    0x0A58: ("STEERING_WHEEL_HEAT", "Steering wheel heating level"),
    0x1051: ("SHOWROOM_AUTO_SHOW", "AutoShow / Showroom display mode"),
    0x1070: ("COMFORT_LIGHT_SETTINGS", "Interior lighting & welcome light settings"),
    0x0A47: ("DRIVE_MODE_PROFILE", "Drive mode profile configuration"),
    0x020E: ("DOOR_LOCK_SETTING", "Speed-sensing auto door lock"),
    0x0211: ("REMOTE_KEY_WINDOW", "Remote key fob window convenience open/close"),
    # 0x7353 Telemetry
    0xFE4C: ("BATTERY_12V_VOLTAGE", "12V auxiliary lead-acid battery voltage"),
    0xFE60: ("BATTERY_HV_SOC", "High-voltage traction battery state-of-charge (%)"),
    0xFE61: ("BATTERY_HV_SOH", "High-voltage traction battery state-of-health (%)"),
    0xFE6D: ("TRACTION_DCDC_STATE", "HV battery & DC-DC converter operating state"),
    0xFE77: ("CHARGING_OBC_STATUS", "On-board charger cable & charging state"),
    0xFE1C: ("DOOR_HOOD_LATCHES", "Vehicle doors, hood, and trunk ajar status"),
    0xFE1D: ("WINDOW_POSITIONS", "Power window open percentage positions"),
    0xFE1E: ("SUNROOF_POSITION", "Panoramic sunroof tilt/slide position"),
    0xFE1F: ("TPMS_TIRE_STATUS", "Tire pressure and temperature monitoring data"),
}


def clean_hex_string(hex_str: str) -> bytes:
    """Strip prefixes, whitespace, and delimiters from a hex string."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", hex_str)
    if len(cleaned) % 2 != 0:
        raise ValueError(f"Hex string has odd length: {len(cleaned)} chars")
    return bytes.fromhex(cleaned)


def decode_parameter_value(param_id: int, val_bytes: bytes) -> str:
    """Decode raw parameter bytes into human-readable engineering units."""
    if param_id == 0xFE4C and len(val_bytes) >= 2:
        raw_val = (val_bytes[0] << 8) | val_bytes[1]
        volts = raw_val / 10.0
        return f"{volts:.1f} V (raw: 0x{raw_val:04X} = {raw_val})"
    elif param_id in (0xFE60, 0xFE61) and len(val_bytes) >= 1:
        return f"{val_bytes[0]}% (raw: 0x{val_bytes[0]:02X})"
    elif param_id in (0x1042, 0x1043) and len(val_bytes) >= 1:
        temp = val_bytes[0]
        return f"{temp}°C (raw: 0x{temp:02X})"
    elif param_id == 0x1044 and len(val_bytes) >= 1:
        return f"Level {val_bytes[0]} (raw: 0x{val_bytes[0]:02X})"
    elif param_id == 0x1046 and len(val_bytes) >= 1:
        modes = {0: "Off", 1: "A/C On", 2: "A/C Max"}
        return modes.get(val_bytes[0], f"Unknown (0x{val_bytes[0]:02X})")
    elif param_id == 0x1047 and len(val_bytes) >= 1:
        modes = {1: "Fresh Air", 2: "Recirculation", 3: "Auto"}
        return modes.get(val_bytes[0], f"Unknown (0x{val_bytes[0]:02X})")
    elif param_id == 0x1051 and len(val_bytes) >= 1:
        modes = {0: "Disabled", 1: "Showroom Mode Active"}
        return modes.get(val_bytes[0], f"Unknown (0x{val_bytes[0]:02X})")
    else:
        return f"Hex: {val_bytes.hex().upper()}"


def dissect_packet(raw_bytes: bytes) -> Dict[str, any]:
    """Dissect a raw framed or unadorned packet."""
    is_framed = False
    if len(raw_bytes) >= 4 and raw_bytes[:2] == b"\x7b\x7b" and raw_bytes[-2:] == b"\x7d\x7d":
        is_framed = True
        inner = raw_bytes[2:-2]
    else:
        inner = raw_bytes

    if len(inner) < 5:
        raise ValueError(f"Packet too short to parse: {len(inner)} bytes (minimum is 5)")

    subsystem = inner[0]
    cmd_id = (inner[1] << 8) | inner[2]
    payload_len = inner[3] | (inner[4] << 8)
    payload_data = inner[5:]

    subsystem_str = SUBSYSTEM_NAMES.get(subsystem, f"Unknown (0x{subsystem:02X})")
    cmd_str = OPCODE_NAMES.get(cmd_id, f"UNKNOWN_CMD_0x{cmd_id:04X}")

    param_id: Optional[int] = None
    param_name: Optional[str] = None
    param_desc: Optional[str] = None
    param_val_str: Optional[str] = None

    if len(payload_data) >= 3:
        param_id = (payload_data[0] << 8) | payload_data[1]
        data_len = payload_data[2]
        val_bytes = payload_data[3 : 3 + data_len]
        if param_id in PARAM_NAMES:
            param_name, param_desc = PARAM_NAMES[param_id]
        else:
            param_name = f"PARAM_0x{param_id:04X}"
            param_desc = "Custom / Unregistered Parameter"
        param_val_str = decode_parameter_value(param_id, val_bytes)

    return {
        "framed": is_framed,
        "subsystem_id": subsystem,
        "subsystem_name": subsystem_str,
        "cmd_id": cmd_id,
        "cmd_name": cmd_str,
        "payload_len": payload_len,
        "payload_bytes": payload_data,
        "param_id": param_id,
        "param_name": param_name,
        "param_desc": param_desc,
        "param_val_str": param_val_str,
        "raw_hex": raw_bytes.hex().upper(),
    }


def format_dissection(d: Dict[str, any]) -> str:
    """Format dissection dictionary as a clean CLI report."""
    lines = [
        "═" * 60,
        f"  Chery MCU Protocol Dissection  [{'Framed {{ }}' if d['framed'] else 'Unframed Payload'}]",
        "═" * 60,
        f"  Raw Hex:        {d['raw_hex']}",
        f"  Subsystem:      0x{d['subsystem_id']:02X} ({d['subsystem_name']})",
        f"  Command Opcode: 0x{d['cmd_id']:04X} ({d['cmd_name']})",
        f"  Payload Length: {d['payload_len']} bytes",
    ]
    if d["param_id"] is not None:
        lines.extend([
            f"  Parameter ID:   0x{d['param_id']:04X} ({d['param_name']})",
            f"  Description:    {d['param_desc']}",
            f"  Decoded Value:  {d['param_val_str']}",
        ])
    lines.append("═" * 60)
    return "\n".join(lines)


def craft_packet(
    cmd_id: int,
    subsystem: Optional[int] = None,
    param_id: Optional[int] = None,
    data_bytes: bytes = b"",
) -> bytes:
    """Craft a fully-framed on-wire packet ({{ ... }})."""
    if subsystem is None:
        subsystem = (cmd_id >> 8) & 0x07

    cmd_msb = (cmd_id >> 8) & 0xFF
    cmd_lsb = cmd_id & 0xFF

    inner_payload = bytearray()
    if param_id is not None:
        inner_payload.append((param_id >> 8) & 0xFF)
        inner_payload.append(param_id & 0xFF)
        inner_payload.append(len(data_bytes))
        inner_payload.extend(data_bytes)
    else:
        inner_payload.extend(data_bytes)

    length_val = len(inner_payload)
    len_lsb = length_val & 0xFF
    len_msb = (length_val >> 8) & 0xFF

    packet = bytearray()
    packet.extend(b"\x7b\x7b")  # {{
    packet.append(subsystem)
    packet.append(cmd_msb)
    packet.append(cmd_lsb)
    packet.append(len_lsb)
    packet.append(len_msb)
    packet.extend(inner_payload)
    packet.extend(b"\x7d\x7d")  # }}

    return bytes(packet)


def main():
    parser = argparse.ArgumentParser(
        description="Chery Tiggo 4 / DeSay MCU Protocol Dissector & Packet Crafting Tool"
    )
    subparsers = parser.add_subparsers(dest="action", help="Subcommand to execute")

    # Dissect command
    dissect_parser = subparsers.add_parser("dissect", help="Dissect a hex packet or log line")
    dissect_parser.add_argument("packet", type=str, help="Hex string or log line containing packet")

    # Craft command
    craft_parser = subparsers.add_parser("craft", help="Craft a framed command packet")
    craft_parser.add_argument("--cmd", type=lambda x: int(x, 0), default=0x7523, help="Opcode (e.g. 0x7523)")
    craft_parser.add_argument("--subsystem", type=lambda x: int(x, 0), default=None, help="Subsystem ID (1-6)")
    craft_parser.add_argument("--param", type=lambda x: int(x, 0), default=None, help="Parameter ID (e.g. 0xFE4C)")
    craft_parser.add_argument("--val-hex", type=str, default="", help="Value bytes in hex (e.g. 01, 107A)")
    craft_parser.add_argument("--query-12v", action="store_true", help="Craft query for 12V battery voltage")
    craft_parser.add_argument("--query-soc", action="store_true", help="Craft query for HV battery SOC")
    craft_parser.add_argument("--ping", action="store_true", help="Craft standard keepalive heartbeat frame")

    # Analyze logcat
    log_parser = subparsers.add_parser("analyze-logcat", help="Extract and summarize Spi2Ethenet traffic from logcat")
    log_parser.add_argument("--file", type=str, required=True, help="Path to logcat text file")
    log_parser.add_argument("--limit", type=int, default=20, help="Maximum number of packets to print")

    args = parser.parse_args()

    if args.action == "dissect":
        raw_input = args.packet
        # Check if user passed log line: Write: 05 75 23... or Read: 03 73 53...
        m = re.search(r"(?:Write|Read):\s+([0-9A-Fa-f\s]+)", raw_input)
        if m:
            raw_input = m.group(1)
        try:
            b = clean_hex_string(raw_input)
            result = dissect_packet(b)
            print(format_dissection(result))
        except Exception as e:
            print(f"Error dissecting packet: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.action == "craft":
        if args.ping:
            # Heartbeat ping: 7B 7B 02 02 05 03 00 00 02 7D 7D
            pkt = craft_packet(cmd_id=0x0205, subsystem=0x02, data_bytes=b"\x00\x02")
        elif args.query_12v:
            # Query 12V: Cmd 0x7523, Subsystem 5, Param 0xFFFE, Val 0x01 0x4C
            pkt = craft_packet(cmd_id=0x7523, subsystem=0x05, param_id=0xFFFE, data_bytes=b"\x01\x4c")
        elif args.query_soc:
            # Query SOC: Cmd 0x7523, Subsystem 5, Param 0xFFFE, Val 0x01 0x60
            pkt = craft_packet(cmd_id=0x7523, subsystem=0x05, param_id=0xFFFE, data_bytes=b"\x01\x60")
        else:
            val_bytes = bytes.fromhex(args.val_hex) if args.val_hex else b""
            pkt = craft_packet(cmd_id=args.cmd, subsystem=args.subsystem, param_id=args.param, data_bytes=val_bytes)

        hex_repr = " ".join(f"{b:02X}" for b in pkt)
        print(f"Framed Hex:   {hex_repr}")
        print(f"Compact Hex:  {pkt.hex().upper()}")
        print("\nDissection Verification:")
        print(format_dissection(dissect_packet(pkt)))

    elif args.action == "analyze-logcat":
        print(f"Analyzing {args.file} for Spi2Ethenet traffic...")
        count = 0
        try:
            with open(args.file, "r", errors="ignore") as f:
                for line in f:
                    if "Spi2Ethenet" not in line:
                        continue
                    m = re.search(r"(Write|Read):\s+([0-9A-Fa-f\s]+)", line)
                    if not m:
                        continue
                    direction = m.group(1)
                    hex_str = m.group(2).strip()
                    try:
                        b = clean_hex_string(hex_str)
                        d = dissect_packet(b)
                        arrow = "→ [TX/Host]" if direction == "Write" else "← [RX/MCU ]"
                        param_info = f"{d['param_name']} = {d['param_val_str']}" if d["param_id"] else d["cmd_name"]
                        print(f"{arrow} Sub:0x{d['subsystem_id']:02X} | Cmd:0x{d['cmd_id']:04X} ({d['cmd_name']}) | {param_info}")
                        count += 1
                        if count >= args.limit:
                            print(f"\nReached limit of {args.limit} packets.")
                            break
                    except Exception:
                        continue
        except Exception as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
