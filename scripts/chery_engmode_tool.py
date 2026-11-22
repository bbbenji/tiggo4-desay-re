#!/usr/bin/env python3
"""
chery_engmode_tool.py  --  DeSay/Chery IVI (SVEngMode) eng-mode / ADB unlock helper.

Reconstructs, from static analysis of:
  * com.desaysv.engmode (SVEngMode.apk) -> MainActivity.parseSnPassword
  * assets/app-sys-release.apk (custom plugin) -> AESUtils / QRCodeDialog / EncryptUtils

Three things it does:

  1. password   -- compute the Engineering-Mode dialog password from the serial.
                   Faithful to parseSnPassword(): the cybersecurity=0 branch is fully
                   static ( last6( lastSixDigits(serial) * 802018 ) ). The
                   cybersecurity=1 branch is a live challenge-response and is only
                   reproduced here as a formula (needs the on-screen random + bus key).

  2. aes        -- the plugin's "Input AES Key" (EngSecKey.txt) primitive. The file is
                   decrypted with a HARDCODED AES-128 key recovered from AESUtils, so the
                   whole challenge file can be read AND forged offline:
                     - aes decrypt  <engseckey.txt>       -> recover plaintext payload
                     - aes craft    <payload-hex> [ivhex] -> emit a valid EngSecKey.txt
                   (The device then re-encrypts the recovered payload under a per-device
                    AndroidKeyStore key before sending it to the MCU, so this primitive
                    forges the *input* side of the handshake, not the device response.)

  3. rsa        -- the primary QR / adbauth.key path. This is RSA-signature gated
                   (EncryptUtils.verify, NONEwithRSA, embedded PUBLIC key only), so it is
                   NOT forgeable offline. This subcommand only shows the signed message
                   format ("18_"+serial+date) and verifies a candidate adbauth.key so you
                   can confirm a vendor-issued key before use.

No device or network is touched. Pure local crypto.

Requires: cryptography  (pip install cryptography)
"""

import sys
import argparse
import base64
from datetime import date

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, serialization

# --------------------------------------------------------------------------
# Hardcoded AES key, recovered from AESUtils.parseAESKey()/decodeData()
# in assets/app-sys-release.apk.
#
#   defaultAESKeyByte (23 bytes):
#     69 5f 24 79 e1 b2 36 0c 2f 70 92 28 71 2f 2a 7d 8d f0 0f 1b 85 24 63
#   parseAESKey(): out[0:9]=blob[2:11], out[9:16]=blob[14:21]
#   decodeData():  out[i] = (out[i] - ((i % 9) + 1)) & 0xFF
# --------------------------------------------------------------------------
_DEFAULT_AES_KEY_BLOB = [
    0x69, 0x5f, 0x24, 0x79, 0xe1, 0xb2, 0x36, 0x0c, 0x2f, 0x70, 0x92, 0x28,
    0x71, 0x2f, 0x2a, 0x7d, 0x8d, 0xf0, 0x0f, 0x1b, 0x85, 0x24, 0x63,
]


def recover_aes_key() -> bytes:
    blob = _DEFAULT_AES_KEY_BLOB
    out = [0] * 16
    out[0:9] = blob[2:11]
    out[9:16] = blob[14:21]
    key = bytes((out[i] - ((i % 9) + 1)) & 0xFF for i in range(16))
    return key


AES_KEY = recover_aes_key()                      # 2377deae31062868 89297b8aec0a157e
ENCRYPT_IV = b"1234567890123456"                 # AESUtils fixed IV ('1'..'6')

# EncryptUtils.PUBLIC_KEY (X.509 SubjectPublicKeyInfo, base64) -- QR/adbauth.key path.
RSA_PUBLIC_KEY_B64 = (
    "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA50ZLpDBDwth5Y1EI4Y6yykxWQIqm0LWCE"
    "koA52YcpPJDsscuxZToki6a5l+8GFLlMxvhg2I8ph+bThsngy2wmL5Ip3NNFwCYrRIvowfofQ+Ult"
    "Chkn/bK3VE3+3Ndb9RLbU9O3ocXhiH24tWKFBh6+cglMDolsgjOjPDN6HfJiWXEx/hOXREF9dqLy8"
    "/xemHHdVLErf3WmyNFgTdagn/VCnERfzNZoi4O4Oa4Jk3/puVLJCMjT/iS4F+xXSr16fwNZcIiqNn"
    "6oda702BAFKcPe+Zn+NyhrShSL36DRvCwlpAMdqz6+KaC8mZZPZthcHfYJP1jciZDUJ6XKMJY3Bc9"
    "bCFDPIbRfK+a3HSU46U8hvuDVfP+DQw8u+URKlY1ISWyjTKr6W1GhU1Fb9Y2MDhgoqMc20pD4C9HD"
    "lg4Cloc30Oasc3TRcL+cGIG1donPLF/wsA3e9nOOEKUx7IxUQ1ly7DUtWMj/VbRV6GgXkEBKR9EFD"
    "xcRspuIZmMVo8ktAf23aNW4AERdaBTsoILMMc5KBc9k9eLzIBP0H4N3wAp1B1PowMGFY+JFQaCKLN"
    "mOxiyfCVN4eIn641xvJySuEgLCpdD3SOGJN7kuM2Is3I2ZpIHRtVzhXdc1IGOK9d8xFjkTWZhaL6l"
    "+5ypQ+EOAoPu9m+5QHnOWfnTU/68kDPGPsCAwEAAQ=="
)

# --------------------------------------------------------------------------
# Stage 3 -- Engineering-Mode dialog password  (MainActivity.parseSnPassword)
# --------------------------------------------------------------------------
PASSWORD_MULTIPLIER = 802018   # constant in the cybersecurity=0 branch
CHERY_PREFIX = "Chery@"        # manager.d.l (public static final) -> f5982l
# manager.d.d() ConstantCode: h() ? 0x8a605 : 0xc3ce2, where
# h() = Build.DISPLAY contains "SOP" or "OTA".
CONSTANT_CODE_SOP = 0x8a605    # 566277  (this unit: Build.DISPLAY has "SOP")
CONSTANT_CODE_OTHER = 0xc3ce2  # 801506


def _sn_tail(serial: str) -> str:
    """parseSnPassword takes the last 6 CHARS of getSN() and requires them numeric."""
    if serial is None or len(serial) < 6:
        raise ValueError(f"serial {serial!r} too short (need >=6 chars)")
    tail = serial[-6:]
    if tail == "000000" or not tail.isdigit():
        raise ValueError(f"last 6 chars of {serial!r} are {tail!r} (non-numeric) -> "
                         "parseSnPassword would return '000000'")
    return tail


def eng_password_basic(serial: str) -> str:
    """cybersecurity=0 branch:  last6( int(last6chars(serial)) * 802018 )."""
    product = int(_sn_tail(serial)) * PASSWORD_MULTIPLIER
    return str(product)[-6:]


def eng_password_cyber(serial: str, random_number: int, constant_code: int) -> str:
    """
    cybersecurity=1, NON-"Version 3.0" branch (parseSnPassword):
        "Chery@" + last6( int(last6chars(serial)) * ConstantCode * randomNumber )
    `random_number` is the challenge shown in the password dialog.
    """
    product = int(_sn_tail(serial)) * constant_code * random_number
    return CHERY_PREFIX + str(product)[-6:]


STOCK_CMAC_KEY = bytes.fromhex("31697060FAA6B4D5A5AD1C4D9A3BD07A")


def eng_password_v3(vin: str, random_number: str, cmac_key: bytes = STOCK_CMAC_KEY) -> str:
    """
    cybersecurity=1, "Version 3.0" branch (parseSnPassword):
        lower6( AES-CMAC_k( VIN + ranNum + "0123" ) )
    The CMAC key is provisioned to the MCU via Input AESKEY (VDB 0x4413).
    Stock key: 31697060FAA6B4D5A5AD1C4D9A3BD07A
    """
    from cryptography.hazmat.primitives.cmac import CMAC as CryptoMAC
    msg = (vin + random_number + "0123").encode("utf-8")
    c = CryptoMAC(algorithms.AES(cmac_key))
    c.update(msg)
    mac_hex = c.finalize().hex()
    return mac_hex[-6:]


# --------------------------------------------------------------------------
# AES primitives  (AESUtils)   -- AES-128-CBC/PKCS7, hardcoded key
# --------------------------------------------------------------------------
def _aes_cbc_decrypt(ct: bytes, iv: bytes) -> bytes:
    dec = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return unpad.update(padded) + unpad.finalize()


def _aes_cbc_encrypt(pt: bytes, iv: bytes) -> bytes:
    pad = padding.PKCS7(128).padder()
    padded = pad.update(pt) + pad.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


def engseckey_decrypt(file_hex: str) -> bytes:
    """
    Reproduce AdbSwitchActivityModel.lambda$inputAESKey$0 + decryptDataCs(ct, iv):
      file bytes = [16-byte IV][ciphertext], hex-encoded as one string.
      IV   = first 16 bytes; ciphertext = the rest.
    """
    raw = bytes.fromhex(file_hex.strip())
    if len(raw) < 32:
        raise ValueError("EngSecKey content too short (need >=16B IV + >=16B ct)")
    iv, ct = raw[:16], raw[16:]
    return _aes_cbc_decrypt(ct, iv)


def engseckey_craft(payload: bytes, iv: bytes) -> str:
    """Emit a valid EngSecKey.txt (hex of [IV][AES-CBC(payload)]) the device will decrypt."""
    if len(iv) != 16:
        raise ValueError("IV must be 16 bytes")
    ct = _aes_cbc_encrypt(payload, iv)
    return (iv + ct).hex()


# --------------------------------------------------------------------------
# RSA / QR path  (EncryptUtils.verify, QRCodeDialog)  -- read-only
# --------------------------------------------------------------------------
def signed_message(serial: str, day: str) -> bytes:
    """
    The exact bytes EncryptUtils.verify() is given as 'data' in QRCodeDialog$3:
    the "18_" prefix + serial + today's date string. (Field layout reconstructed from
    the dialog; verify against your own device's log line 'actionAuth content = ...'.)
    """
    return (f"18_{serial}{day}").encode()


def _load_rsa_public():
    return serialization.load_der_public_key(base64.b64decode(RSA_PUBLIC_KEY_B64))


def verify_none_with_rsa(pub, message: bytes, signature: bytes) -> bool:
    """
    Java "NONEwithRSA": RSA PKCS#1 v1.5 where the signed octet string IS the raw
    message (no hash, no DigestInfo). The cryptography high-level API cannot express
    "no hash", so do the public operation by hand:
        m = sig^e mod n ;  strip EMSA-PKCS1-v1_5 padding ( 00 01 FF..FF 00 || M ) ;
        accept iff the recovered M equals `message`.
    """
    nums = pub.public_numbers()
    n, e = nums.n, nums.e
    k = (n.bit_length() + 7) // 8
    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False
    em = pow(sig_int, e, n).to_bytes(k, "big")
    # EMSA-PKCS1-v1_5: 0x00 0x01 PS(0xFF..) 0x00 M
    if len(em) < 11 or em[0] != 0x00 or em[1] != 0x01:
        return False
    try:
        sep = em.index(0x00, 2)
    except ValueError:
        return False
    if sep < 10:                       # PS must be >= 8 bytes of 0xFF
        return False
    if any(b != 0xFF for b in em[2:sep]):
        return False
    return em[sep + 1:] == message


def verify_adbauth_key(serial: str, day: str, adbauth_key_bytes: bytes) -> bool:
    return verify_none_with_rsa(_load_rsa_public(), signed_message(serial, day),
                                adbauth_key_bytes)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("password", help="eng-mode dialog password from serial")
    pw.add_argument("serial", help="vehicle serial / SN = getSN(); last 6 chars must be digits")
    pw.add_argument("--random", type=int, default=None,
                    help="on-screen challenge number -> compute the live cyber=1 password")
    pw.add_argument("--display", default="SOP",
                    help="Build.DISPLAY (picks ConstantCode: has SOP/OTA ->566789 else 802018)")

    pv3 = sub.add_parser("v3password", help="Version 3.0 eng password (AES-CMAC)")
    pv3.add_argument("vin", help="17-char VIN (e.g. LNNBBDEG1TC029838)")
    pv3.add_argument("rannum", help="4-digit ranNum from screen")
    pv3.add_argument("--key", default=STOCK_CMAC_KEY.hex(),
                     help="CMAC key hex (default: stock 31697060FAA6B4D5A5AD1C4D9A3BD07A)")

    pa = sub.add_parser("aes", help="EngSecKey.txt decrypt / craft (hardcoded AES key)")
    pa_sub = pa.add_subparsers(dest="aescmd", required=True)
    pad_ = pa_sub.add_parser("decrypt", help="decrypt an EngSecKey.txt (hex)")
    pad_.add_argument("file", help="path to EngSecKey.txt, or '-' for stdin")
    pac = pa_sub.add_parser("craft", help="craft a valid EngSecKey.txt")
    pac.add_argument("payload_hex", help="plaintext payload as hex")
    pac.add_argument("iv_hex", nargs="?", default="00" * 16, help="16-byte IV hex (default zeros)")
    pa_sub.add_parser("key", help="print the recovered AES key")

    pr = sub.add_parser("rsa", help="QR/adbauth.key path (RSA, verify only)")
    pr_sub = pr.add_subparsers(dest="rsacmd", required=True)
    prm = pr_sub.add_parser("message", help="show the signed message 18_<serial><date>")
    prm.add_argument("serial")
    prm.add_argument("day", nargs="?", default=date.today().strftime("%Y%m%d"))
    prv = pr_sub.add_parser("verify", help="verify a candidate adbauth.key")
    prv.add_argument("serial")
    prv.add_argument("day")
    prv.add_argument("adbauth_key", help="path to adbauth.key (raw signature bytes)")

    args = p.parse_args(argv)

    if args.cmd == "password":
        tail = _sn_tail(args.serial)
        disp = args.display or ""
        cc = CONSTANT_CODE_SOP if ("SOP" in disp or "OTA" in disp) else CONSTANT_CODE_OTHER
        print(f"serial (getSN)         : {args.serial}")
        print(f"last 6 chars           : {tail}")
        print(f"ConstantCode (DISPLAY) : {cc}  (Build.DISPLAY={disp!r})")
        print(f"password (cyber=0)     : {eng_password_basic(args.serial)}")
        print( "  = last6( int(last6) * 802018 )")
        print()
        if args.random is not None:
            pw = eng_password_cyber(args.serial, args.random, cc)
            print(f"password (cyber=1, non-3.0, random={args.random}) : {pw}")
            print( "  = 'Chery@' + last6( int(last6) * ConstantCode * random )")
        else:
            print("password (cyber=1)     : needs --random (the on-screen challenge). Branches:")
            print("  Version 3.0 : lower6( AES-CMAC_k( VIN + random + '0123' ) )  [use 'v3password' cmd; stock key known]")
            print("  else        : 'Chery@' + last6( int(last6) * ConstantCode * random )  [pass --random]")

    elif args.cmd == "v3password":
        cmac_key = bytes.fromhex(args.key)
        pwd = eng_password_v3(args.vin, args.rannum, cmac_key)
        print(f"VIN       : {args.vin}")
        print(f"ranNum    : {args.rannum}")
        print(f"CMAC key  : {cmac_key.hex()}")
        print(f"message   : {args.vin + args.rannum + '0123'}")
        print(f"password  : {pwd}")

    elif args.cmd == "aes":
        if args.aescmd == "key":
            print("AES-128 key :", AES_KEY.hex())
            print("mode        : AES/CBC/PKCS7Padding")
            print("encrypt IV  :", ENCRYPT_IV.hex(), f"({ENCRYPT_IV.decode()})")
        elif args.aescmd == "decrypt":
            data = sys.stdin.read() if args.file == "-" else open(args.file).read()
            pt = engseckey_decrypt(data)
            print("plaintext hex   :", pt.hex())
            try:
                print("plaintext ascii :", pt.decode())
            except UnicodeDecodeError:
                print("plaintext ascii : <non-text>")
        elif args.aescmd == "craft":
            payload = bytes.fromhex(args.payload_hex)
            iv = bytes.fromhex(args.iv_hex)
            out = engseckey_craft(payload, iv)
            print(out)

    elif args.cmd == "rsa":
        if args.rsacmd == "message":
            msg = signed_message(args.serial, args.day)
            print("signed message :", msg.decode())
            print("bytes hex      :", msg.hex())
            print("NOTE: adbauth.key is an RSA signature over this with Chery's PRIVATE")
            print("      key -- only the public key is on the device, so it is NOT forgeable.")
        elif args.rsacmd == "verify":
            sig = open(args.adbauth_key, "rb").read().strip()
            # adbauth.key on disk may be base64 or hex; try raw, then b64, then hex.
            for conv in (lambda x: x,
                         lambda x: base64.b64decode(x),
                         lambda x: bytes.fromhex(x.decode())):
                try:
                    ok = verify_adbauth_key(args.serial, args.day, conv(sig))
                except Exception:
                    continue
                if ok:
                    print("VALID adbauth.key for", args.serial, args.day)
                    return 0
            print("invalid / not a signature for this serial+day")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
