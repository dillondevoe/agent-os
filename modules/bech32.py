"""bech32.py — bech32 codec for NIP-19 `npub` encoding (Agent OS Phase 1.5B, task 287 item 2).

PROVENANCE: vendored from the BIP-173 reference implementation,
https://github.com/sipa/bech32/blob/master/ref/python/segwit_addr.py
No codec logic originated here — same standard as modules/bip340.py, and Geist AST-diffs both
against upstream, so the only edits are subtractions and this header:
  1. dropped `decode()`/`encode()` — those are segwit ADDRESS helpers (witness programs), a
     different job from encoding a Nostr pubkey; carrying them would invite misuse;
  2. kept `Encoding`, `CHARSET`, `BECH32M_CONST`, `bech32_polymod`, `bech32_hrp_expand`,
     `bech32_verify_checksum`, `bech32_create_checksum`, `bech32_encode`, `bech32_decode`,
     `convertbits` byte-for-byte — all TEN, counted against the file rather than from memory
     (Geist's #120 nit: the first version of this list said nine and omitted `BECH32M_CONST`);
  3. added this header and `npub_encode`/`npub_decode` below, which are OURS — thin NIP-19
     wrappers containing no codec arithmetic.

NIP-19 `npub` is **bech32, not bech32m** (BECH32M is the newer segwit-v1 variant). The two differ
only by a checksum constant, so a wrong pick still produces a plausible-looking string that every
other Nostr implementation rejects — a silent-mismatch class rather than a crash. Pinned by
vectors in tests/identity-battery.py.

LICENSE: MIT. Copyright (c) 2017, 2020 Pieter Wuille. Verified in the upstream file header
before vendoring, per spec §3; no AGPL in this lane.
"""

from enum import Enum

class Encoding(Enum):
    """Enumeration type to list the various supported encodings."""
    BECH32 = 1
    BECH32M = 2

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32M_CONST = 0x2bc830a3

def bech32_polymod(values):
    """Internal function that computes the Bech32 checksum."""
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def bech32_hrp_expand(hrp):
    """Expand the HRP into values for checksum computation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def bech32_verify_checksum(hrp, data):
    """Verify a checksum given HRP and converted data characters."""
    const = bech32_polymod(bech32_hrp_expand(hrp) + data)
    if const == 1:
        return Encoding.BECH32
    if const == BECH32M_CONST:
        return Encoding.BECH32M
    return None

def bech32_create_checksum(hrp, data, spec):
    """Compute the checksum values given HRP and data."""
    values = bech32_hrp_expand(hrp) + data
    const = BECH32M_CONST if spec == Encoding.BECH32M else 1
    polymod = bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def bech32_encode(hrp, data, spec):
    """Compute a Bech32 string given HRP and data values."""
    combined = data + bech32_create_checksum(hrp, data, spec)
    return hrp + '1' + ''.join([CHARSET[d] for d in combined])

def bech32_decode(bech):
    """Validate a Bech32/Bech32m string, and determine HRP and data."""
    if ((any(ord(x) < 33 or ord(x) > 126 for x in bech)) or
            (bech.lower() != bech and bech.upper() != bech)):
        return (None, None, None)
    bech = bech.lower()
    pos = bech.rfind('1')
    if pos < 1 or pos + 7 > len(bech) or len(bech) > 90:
        return (None, None, None)
    if not all(x in CHARSET for x in bech[pos+1:]):
        return (None, None, None)
    hrp = bech[:pos]
    data = [CHARSET.find(x) for x in bech[pos+1:]]
    spec = bech32_verify_checksum(hrp, data)
    if spec is None:
        return (None, None, None)
    return (hrp, data[:-6], spec)

def convertbits(data, frombits, tobits, pad=True):
    """General power-of-2 base conversion."""
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


# ---- NIP-19 (ours, no codec arithmetic — thin wrappers over the vendored core) --------------
NPUB_HRP = "npub"

def npub_encode(pubkey: bytes) -> str:
    """32-byte x-only BIP-340 pubkey -> NIP-19 npub string."""
    if len(pubkey) != 32:
        raise ValueError("a BIP-340 x-only pubkey is 32 bytes, got %d" % len(pubkey))
    data = convertbits(pubkey, 8, 5)
    if data is None:
        raise ValueError("bit conversion failed")
    out = bech32_encode(NPUB_HRP, data, Encoding.BECH32)
    if out is None:
        raise ValueError("bech32 encoding failed")
    return out

def npub_decode(npub: str) -> bytes:
    """NIP-19 npub string -> 32-byte x-only pubkey. Raises on anything malformed."""
    hrp, data, spec = bech32_decode(npub)
    if hrp != NPUB_HRP or data is None:
        raise ValueError("not a valid npub")
    # Reject bech32m explicitly: it decodes cleanly under its own checksum, so accepting it
    # here would silently admit strings no other Nostr implementation will honor.
    if spec != Encoding.BECH32:
        raise ValueError("npub must be bech32, not bech32m")
    raw = convertbits(data, 5, 8, False)
    if raw is None or len(raw) != 32:
        raise ValueError("npub payload is not a 32-byte pubkey")
    return bytes(raw)
