#!/usr/bin/env python3
"""
Chery Tiggo 4 (2025) / DeSay SV Automotive Head Unit
Vehicle Configuration (CarConfigUtil / EolConfig / VDBus) Decoder & Modifier Tool

Reverse engineered from com.desaysv.ivi.extra.project.carconfig and libdesaysv_vehicledevice.so.
Supports decoding and crafting vehicle.persist.* configurations, EOL bitfields,
brand theming, phone projection (CarPlay / Android Auto), ADAS feature gating, and comfort options.
"""

import sys
import argparse
import json
import subprocess
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Brand Definitions (Config ID 0xB7 in mComboConfig[6])
# ---------------------------------------------------------------------------
BRANDS = {
    1: "Chery",
    2: "Mexico Chery (Chirey)",
    3: "Italian Chery",
    4: "OMODA",
    5: "Central Asia",
    6: "JAECOO",
    7: "Lucano",
    8: "EBRO",
    9: "Tenet",
    10: "Onives",
    12: "Esteo",
}
BRAND_BY_NAME = {v.lower().split()[0]: k for k, v in BRANDS.items()}
BRAND_BY_NAME["chirey"] = 2
BRAND_BY_NAME["jaecoo"] = 6
BRAND_BY_NAME["omoda"] = 4
BRAND_BY_NAME["ebro"] = 8

# ---------------------------------------------------------------------------
# Vehicle Model Code Definitions (Config ID 0xA0 in mComboConfig[1])
# ---------------------------------------------------------------------------
MODELS = {
    0x01: "T26 (Tiggo 9)",
    0x02: "T22 (Tiggo 7)",
    0x03: "T1EFL / T1E24",
    0x05: "M1E / Arrizo 8",
    0x07: "T19C (Tiggo 5x / Tiggo 4 Pro)",
    0x08: "T18P (Tiggo 8 Pro)",
    0x09: "T1EJ (Jaecoo 7)",
    0x0A: "T18FL3 (Tiggo 8)",
    0x0C: "T19FL (Tiggo 4 FL / Tiggo Cross 2025)",
    0x0D: "T28",
    0x0E: "T18FL4 (Tiggo 8 FL4)",
    0x13: "T1G",
    0x14: "T1Q",
}

# ---------------------------------------------------------------------------
# Powertrain Definitions (Config ID 0x9A in mCarConfig5[4] >> 4 & 0x03)
# ---------------------------------------------------------------------------
POWERTRAINS = {
    0: "ICE (Internal Combustion Engine)",
    1: "PHEV (Plug-in Hybrid Electric)",
    2: "EV (Battery Electric Vehicle)",
    3: "HEV (Hybrid Electric Vehicle)",
}

# ---------------------------------------------------------------------------
# Bitfield Layout Rules for Known Key Config IDs
# Format: (config_array_name, byte_index, bit_shift, bit_mask, description)
# ---------------------------------------------------------------------------
CONFIG_SPECS = {
    # Platform & Identification
    0x9A: ("mCarConfig5", 4, 4, 0x03, "Powertrain Type (0=ICE, 1=PHEV, 2=EV, 3=HEV)"),
    0x9B: ("mCarConfig5", 4, 6, 0x01, "Platform Variant (0=T19FL/Tiggo 4, 1=T26)"),
    0xA0: ("mComboConfig", 1, 0, 0xFF, "Vehicle Model Code (e.g. 0x0C=T19FL)"),
    0xB7: ("mComboConfig", 6, 0, 0xFF, "Brand Theme ID (1=Chery, 4=OMODA, 6=JAECOO, 8=EBRO)"),
    0xB9: ("mComboConfig", 6, 0, 0xFF, "CyberSecurity Function Flag"),

    # ADAS Suite
    0x70: ("mCarConfig4", 3, 5, 0x01, "Master Full ADAS Suite Flag (1=Enables all 13 ADAS systems)"),
    0x54: ("mCarConfig4", 2, 0, 0x01, "Forward Collision Warning (FCW)"),
    0x1B: ("mCarConfig4", 1, 0, 0x01, "Autonomous Emergency Braking (AEB)"),
    0x14: ("mCarConfig4", 0, 0, 0x01, "Lane Departure Warning (LDW)"),
    0x10: ("mCarConfig3", 7, 0, 0x01, "Lane Departure Warning Secondary Flag"),
    0x4F: ("mCarConfig4", 0, 4, 0x01, "Lane Keeping Assist / Intervention (LDP/LKA)"),
    0x4E: ("mCarConfig4", 0, 2, 0x01, "Emergency Lane Keeping (ELK)"),
    0x49: ("mCarConfig3", 7, 4, 0x01, "Blind Spot Detection (BSD)"),
    0x57: ("mCarConfig4", 2, 6, 0x01, "Rear Cross Traffic Alert (RCTA)"),
    0x80: ("mCarConfig5", 0, 0, 0x01, "Rear Collision Warning (RCW)"),
    0x2E: ("mCarConfig3", 7, 6, 0x01, "Door Opening Warning (DOW)"),
    0x68: ("mCarConfig4", 3, 0, 0x01, "Speed Limit Assist / Traffic Sign Recognition (SLA/TSR)"),
    0x4D: ("mCarConfig3", 6, 0, 0x01, "Intelligent Speed Control / ACC"),
    0x88: ("mCarConfig5", 2, 0, 0x01, "Intelligent High Beam Control (IHC)"),
    0x8E: ("mCarConfig5", 3, 0, 0x01, "Comfort Stop / Brake Pitch Control (CST)"),
    0x9D: ("mCarConfig5", 4, 0, 0x01, "Driver Fatigue Warning System"),

    # Lighting & Ambient Atmosphere
    0x1E: ("mCarConfig6", 2, 7, 0x01, "Ambient Atmosphere Lighting Presence"),
    0x6D: ("mCarConfig4", 4, 0, 0x01, "Multi-color / Drive Mode Dynamic Rhythm Lighting"),
    0x75: ("mCarConfig4", 2, 6, 0x01, "Music Rhythm Lighting Sync"),
    0x0F: ("mCarConfig7", 7, 4, 0x01, "Automatic Headlight / Follow-Me-Home"),

    # Vehicle Comfort & Hardware Options
    0x3B: ("mCarConfig4", 1, 4, 0x01, "Sunroof / Panoramic Glass Roof"),
    0x15: ("mCarConfig4", 0, 2, 0x01, "Power Liftgate (PLG / Electric Tailgate)"),
    0xA4: ("mCarConfig5", 7, 2, 0x03, "Tailgate Hardware Type (0=Manual, 1=Electric, 2=Foot Sensor)"),
    0x89: ("mCarConfig5", 2, 2, 0x01, "Wireless Phone Charging Pad"),
    0x7F: ("mCarConfig4", 6, 0, 0x01, "Driver Seat Electric Memory & Adjustment"),
    0x22: ("mCarConfig6", 3, 0, 0x01, "Seat Heating & Ventilation"),
    0x45: ("mCarConfig3", 6, 2, 0x01, "Seat Massage System"),
    0x7C: ("mCarConfig4", 5, 0, 0x01, "Rearview Mirror Electric Fold & Reverse Tilt"),
    0x28: ("mCarConfig6", 4, 0, 0x01, "PEPS Smart Key (Passive Entry Passive Start)"),
    0x2A: ("mCarConfig6", 4, 4, 0x01, "Smart Key Auto Window Roll-up / Walk-Away Lock"),
    0x95: ("mCarConfig5", 4, 2, 0x01, "Child In Rear (CIR) Detection"),
    0xA8: ("mCarConfig6", 0, 0, 0x01, "Child Presence Detection (CPD)"),
    0x6A: ("mCarConfig4", 3, 2, 0x01, "Windshield Head-Up Display (W-HUD)"),
    0x6C: ("mCarConfig4", 3, 4, 0x01, "Augmented Reality Head-Up Display (AR-HUD)"),
    0xAA: ("mCarConfig6", 1, 0, 0x01, "HD Navigation Map Support"),
}

# ---------------------------------------------------------------------------
# Direct Boolean Properties for Feature Certification / Phone Projection
# ---------------------------------------------------------------------------
PERSIST_BOOLEAN_PROPERTIES = [
    ("vehicle.persist.carplay.certification.enabled", "Apple CarPlay Projection"),
    ("vehicle.persist.androidauto.certification.enabled", "Android Auto Projection"),
    ("vehicle.persist.sys.carlink.wireless.enabled", "Wireless CarLink Phone Link"),
    ("vehicle.persist.sys.carlink.wired.enabled", "Wired CarLink Phone Link"),
    ("vehicle.persist.wifi.certification.enabled", "5GHz / 2.4GHz Wi-Fi AP Certification"),
    ("vehicle.persist.carlife.certification.enabled", "Baidu CarLife Certification"),
    ("vehicle.persist.hicar.certification.enabled", "Huawei HiCar Certification"),
    ("vehicle.persist.dvr.enabled", "Internal Digital Video Recorder (DVR)"),
    ("vehicle.persist.arnavi.enabled", "AR Navigation Display"),
    ("vehicle.persist.apa.enabled", "Automatic Parking Assist (APA)"),
    ("vehicle.persist.avm.enabled", "Around View Monitoring (360 AVM Camera)"),
]


def hex_to_bytes(hex_str: str) -> List[int]:
    """Convert 16-char hex string to list of 8 ints."""
    clean = hex_str.strip().replace(" ", "").replace("0x", "")
    if len(clean) % 2 != 0:
        clean = "0" + clean
    return [int(clean[i : i + 2], 16) for i in range(0, len(clean), 2)]


def bytes_to_hex(byte_list: List[int]) -> str:
    """Convert list of ints to 16-char uppercase hex string."""
    return "".join(f"{b & 0xFF:02X}" for b in byte_list)


class CheryCarConfig:
    def __init__(self):
        self.configs: Dict[str, List[int]] = {
            "mCarConfig1": [0] * 8,
            "mCarConfig2": [0] * 8,
            "mCarConfig3": [0] * 8,
            "mCarConfig4": [0] * 8,
            "mCarConfig5": [0] * 8,
            "mCarConfig6": [0] * 8,
            "mCarConfig7": [0] * 8,
            "mComboConfig": [0] * 8,
        }
        self.persist_props: Dict[str, str] = {}

    def set_config_hex(self, name: str, hex_str: str):
        arr = hex_to_bytes(hex_str)
        if len(arr) < 8:
            arr.extend([0] * (8 - len(arr)))
        self.configs[name] = arr[:8]

    def get_config_val(self, cid: int) -> int:
        if cid not in CONFIG_SPECS:
            return 0
        arr_name, byte_idx, shift, mask, _ = CONFIG_SPECS[cid]
        arr = self.configs.get(arr_name, [0] * 8)
        if byte_idx < len(arr):
            return (arr[byte_idx] >> shift) & mask
        return 0

    def set_config_val(self, cid: int, val: int):
        if cid not in CONFIG_SPECS:
            return
        arr_name, byte_idx, shift, mask, _ = CONFIG_SPECS[cid]
        arr = self.configs.get(arr_name, [0] * 8)
        if byte_idx < len(arr):
            cur = arr[byte_idx]
            cleared = cur & ~(mask << shift)
            arr[byte_idx] = cleared | ((val & mask) << shift)
            self.configs[arr_name] = arr

    def decode_all(self) -> Dict[str, any]:
        report = {}
        # Powertrain & Platform
        p_val = self.get_config_val(0x9A)
        report["powertrain"] = {
            "id": p_val,
            "name": POWERTRAINS.get(p_val, f"Unknown ({p_val})"),
        }
        plat_val = self.get_config_val(0x9B)
        report["platform"] = "T26" if plat_val == 1 else "T19FL (Tiggo 4 FL / Tiggo Cross)"

        # Model Code
        model_val = self.get_config_val(0xA0)
        report["vehicle_model"] = {
            "code": f"0x{model_val:02X}",
            "name": MODELS.get(model_val, f"Unknown Model (0x{model_val:02X})"),
        }

        # Brand Theme
        brand_val = self.get_config_val(0xB7)
        report["brand_theme"] = {
            "id": brand_val,
            "name": BRANDS.get(brand_val, f"Unknown Brand ({brand_val})"),
        }

        # ADAS
        full_adas = self.get_config_val(0x70) == 1
        adas = {
            "master_full_suite_enabled": full_adas,
            "fcw": full_adas or self.get_config_val(0x54) == 1,
            "aeb": full_adas or self.get_config_val(0x1B) == 1,
            "ldw": full_adas or self.get_config_val(0x14) == 1 or self.get_config_val(0x10) == 1,
            "lka_ldp": full_adas or self.get_config_val(0x4F) == 1,
            "elk": full_adas or self.get_config_val(0x4E) == 1,
            "bsd": full_adas or self.get_config_val(0x49) == 1,
            "rcta": full_adas or self.get_config_val(0x57) == 1,
            "rcw": full_adas or self.get_config_val(0x80) == 1,
            "dow": full_adas or self.get_config_val(0x2E) == 1,
            "tsr_sla": full_adas or self.get_config_val(0x68) == 1,
            "acc_speed_ctrl": full_adas or self.get_config_val(0x4D) == 1,
            "ihc_high_beam": self.get_config_val(0x88) == 1,
            "cst_pitch_ctrl": self.get_config_val(0x8E) == 1,
        }
        report["adas"] = adas

        # Lighting & Comfort
        report["lighting_and_comfort"] = {
            "ambient_light_present": self.get_config_val(0x1E) == 1,
            "dynamic_rhythm_lighting": self.get_config_val(0x6D) == 1,
            "music_rhythm_sync": self.get_config_val(0x75) == 1,
            "sunroof": self.get_config_val(0x3B) == 1,
            "power_tailgate": self.get_config_val(0x15) == 1,
            "tailgate_type": self.get_config_val(0xA4),
            "wireless_charging": self.get_config_val(0x89) == 1,
            "seat_memory": self.get_config_val(0x7F) == 1,
            "seat_heat_vent": self.get_config_val(0x22) == 1,
            "seat_massage": self.get_config_val(0x45) == 1,
            "smart_key_peps": self.get_config_val(0x28) == 1,
            "smart_key_auto_rollup": self.get_config_val(0x2A) == 1,
            "whud": self.get_config_val(0x6A) == 1,
            "arhud": self.get_config_val(0x6C) == 1,
        }

        # Certification
        report["persist_properties"] = self.persist_props
        return report


def generate_adb_script(actions: List[str], cfg: CheryCarConfig) -> str:
    """Generate shell script commands to apply on the head unit."""
    lines = [
        "#!/system/bin/sh",
        "# Chery Tiggo 4 (2025) Feature Unlocking Script",
        "# Execute via ADB root or serial shell",
        "",
    ]

    if "carplay" in actions or "all" in actions:
        lines.append("# --- Enable Apple CarPlay (Wired & Wireless) ---")
        lines.append("setprop vehicle.persist.carplay.certification.enabled true")
        lines.append("setprop persist.sys.carplay.certification true")
        lines.append("setprop vehicle.persist.wifi.certification.enabled true")
        lines.append("")

    if "androidauto" in actions or "all" in actions:
        lines.append("# --- Enable Android Auto (Wired & Wireless) ---")
        lines.append("setprop vehicle.persist.androidauto.certification.enabled true")
        lines.append("setprop persist.sys.androidauto.certification true")
        lines.append("setprop vehicle.persist.wifi.certification.enabled true")
        lines.append("")

    if "carlink" in actions or "all" in actions:
        lines.append("# --- Enable CarLink Phone Mirroring ---")
        lines.append("setprop vehicle.persist.sys.carlink.wireless.enabled true")
        lines.append("setprop vehicle.persist.sys.carlink.wired.enabled true")
        lines.append("")

    if "adas" in actions or "all" in actions:
        lines.append("# --- Enable Full ADAS Suite (Master Flag 0x70) ---")
        cfg.set_config_val(0x70, 1)  # Master ADAS flag in configs4[3] bit 5
        hex4 = bytes_to_hex(cfg.configs["mCarConfig4"])
        lines.append(f"setprop vehicle.persist.project.ext.configs4 {hex4}")
        lines.append("")

    if "ambient" in actions or "all" in actions:
        lines.append("# --- Enable Ambient Lighting & Dynamic Rhythm ---")
        cfg.set_config_val(0x1E, 1)  # Presence in configs6
        cfg.set_config_val(0x6D, 1)  # Dynamic Rhythm in configs4
        cfg.set_config_val(0x75, 1)  # Music Sync in configs4
        hex4 = bytes_to_hex(cfg.configs["mCarConfig4"])
        hex6 = bytes_to_hex(cfg.configs["mCarConfig6"])
        lines.append(f"setprop vehicle.persist.project.ext.configs4 {hex4}")
        lines.append(f"setprop vehicle.persist.project.ext.configs6 {hex6}")
        lines.append("")

    # Broadcast VDBus notification to tell running services to reload config
    lines.append("# --- Trigger VDBus Config Reload Notification (0xE0579) ---")
    lines.append("# Broadcasts PROJECT_VEHICLE_PROPERTY_CONFIG_UPDATE")
    lines.append("am broadcast -a com.desaysv.ivi.vdb.ACTION_CONFIG_UPDATE 2>/dev/null || true")
    lines.append("pkill -f com.desaysv.launcher || true")
    lines.append("pkill -f com.desaysv.vehiclesetting || true")
    lines.append("echo '[+] Applied vehicle configuration successfully.'")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Chery Tiggo 4 (2025) Vehicle Configuration & Feature Unlocking Tool"
    )
    subparsers = parser.add_subparsers(dest="cmd", help="Subcommand to execute")

    # Command: decode
    dec_parser = subparsers.add_parser("decode", help="Decode vehicle hex configs")
    dec_parser.add_argument("--configs4", default="0000000000000000", help="Hex string for configs4")
    dec_parser.add_argument("--configs5", default="0014818239003600", help="Hex string for configs5 (default: stock Tiggo 4 FL)")
    dec_parser.add_argument("--configs6", default="0000000000000000", help="Hex string for configs6")
    dec_parser.add_argument("--configs7", default="0000000000000000", help="Hex string for configs7")
    dec_parser.add_argument("--combo", default="000C000000000100", help="Hex string for combo.config (model=0x0C, brand=0x01)")
    dec_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Command: unlock
    unl_parser = subparsers.add_parser("unlock", help="Generate unlocking script / hex payload")
    unl_parser.add_argument(
        "--features",
        nargs="+",
        choices=["carplay", "androidauto", "carlink", "adas", "ambient", "all"],
        default=["carplay", "androidauto"],
        help="Features to unlock",
    )
    unl_parser.add_argument("--set-brand", choices=list(BRAND_BY_NAME.keys()), help="Change brand theme")
    unl_parser.add_argument("--configs4", default="0000000000000000", help="Base configs4 hex")
    unl_parser.add_argument("--configs5", default="0014818239003600", help="Base configs5 hex")
    unl_parser.add_argument("--configs6", default="0000000000000000", help="Base configs6 hex")
    unl_parser.add_argument("--combo", default="000C000000000100", help="Base combo.config hex")
    unl_parser.add_argument("-o", "--output", help="Save script to output file")

    args = parser.parse_args()

    cfg = CheryCarConfig()

    if args.cmd == "decode":
        cfg.set_config_hex("mCarConfig4", args.configs4)
        cfg.set_config_hex("mCarConfig5", args.configs5)
        cfg.set_config_hex("mCarConfig6", args.configs6)
        cfg.set_config_hex("mCarConfig7", args.configs7)
        cfg.set_config_hex("mComboConfig", args.combo)

        report = cfg.decode_all()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("=" * 65)
            print("Chery Tiggo 4 (2025) / DeSay SV Vehicle Configuration Report")
            print("=" * 65)
            print(f"Vehicle Model:    {report['vehicle_model']['name']} ({report['vehicle_model']['code']})")
            print(f"Platform:        {report['platform']}")
            print(f"Powertrain:      {report['powertrain']['name']}")
            print(f"Brand Theme:     {report['brand_theme']['name']} (ID: {report['brand_theme']['id']})")
            print("-" * 65)
            print("ADAS Suite Status:")
            for k, v in report["adas"].items():
                print(f"  {k:28s}: {'[ENABLED]' if v else '[DISABLED]'}")
            print("-" * 65)
            print("Lighting & Comfort Options:")
            for k, v in report["lighting_and_comfort"].items():
                status = f"[ENABLED] ({v})" if isinstance(v, int) and v > 0 else ('[ENABLED]' if v is True else '[DISABLED]')
                print(f"  {k:28s}: {status}")
            print("=" * 65)

    elif args.cmd == "unlock":
        cfg.set_config_hex("mCarConfig4", args.configs4)
        cfg.set_config_hex("mCarConfig5", args.configs5)
        cfg.set_config_hex("mCarConfig6", args.configs6)
        cfg.set_config_hex("mComboConfig", args.combo)

        if args.set_brand:
            brand_id = BRAND_BY_NAME[args.set_brand.lower()]
            cfg.set_config_val(0xB7, brand_id)
            print(f"[*] Setting Brand Theme to {BRANDS[brand_id]} (ID: {brand_id})")

        script = generate_adb_script(args.features, cfg)
        if args.output:
            with open(args.output, "w") as f:
                f.write(script)
            print(f"[+] Saved unlocking script to {args.output}")
        else:
            print(script)
            print("-" * 65)
            print("Generated Modified Hex Configs:")
            print(f"  vehicle.persist.project.ext.configs4 = {bytes_to_hex(cfg.configs['mCarConfig4'])}")
            print(f"  vehicle.persist.project.ext.configs5 = {bytes_to_hex(cfg.configs['mCarConfig5'])}")
            print(f"  vehicle.persist.project.ext.configs6 = {bytes_to_hex(cfg.configs['mCarConfig6'])}")
            print(f"  vehicle.persist.combo.config        = {bytes_to_hex(cfg.configs['mComboConfig'])}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
