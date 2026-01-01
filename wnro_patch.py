#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Philipp Lopaur
"""
GPS week-number-rollover (WNRO) patch for HP Z3801A firmware Rev 3543.

The Z3801A's 6-channel Motorola VP Oncore reports the date in its @@Ba
message (month, day, 16-bit year). HP's firmware copies the payload to RAM
$100D36 and every consumer of that date goes through one helper:

    0x110FA  date_mdy_to_ymd(long mdy) -> long ymd   (just swaps the words)

This patch replaces that helper with a JMP to new code in unused ROM. The new
code does the same conversion and then, if the date lies in
[LOWER, PIVOT), adds 1024 weeks (7168 days) until it is >= PIVOT, using the
firmware's own MJD routines (0x10F46 ymd->MJD, 0x10E72 MJD->ymd).

Dates before LOWER (no fix yet / receiver defaults) pass through untouched.
Correct dates stay correct until PIVOT + 1024 weeks.

Usage: wnro_patch.py IN.BIN OUT.BIN [--pivot YYYY-MM-DD] [--split]
"""
import argparse, datetime, struct, sys

HOOK_ADDR  = 0x110FA
NEW_ADDR   = 0x61C00           # inside 0xFF-filled gap 0x61B4E..0x7FFFB
YMD2MJD    = 0x10F46
MJD2YMD    = 0x10E72
CKS_REGIONS = ((0x10000, 0x3FFFC), (0x40000, 0x7FFFC))  # (start, checksum addr); hook is in #1, new code in #2
ORIG_HOOK  = bytes.fromhex("4e56fff82d6e0008fffc")   # link a6,#-8 / move.l 8(a6),-4(a6)
LOWER      = datetime.date(1990, 1, 1)


def ymd_packed(d): return (d.year << 16) | (d.month << 8) | d.day
def mjd(d):        return (d - datetime.date(1858, 11, 17)).days


def build_code(pivot):
    c  = bytes.fromhex("202f0004")                                # move.l 4(a7),d0
    c += bytes.fromhex("4840")                                    # swap d0     -> yyyy:mm:dd
    c += bytes.fromhex("0c80") + struct.pack(">I", ymd_packed(pivot))  # cmpi.l #PIVOT,d0
    c += bytes.fromhex("643a")                                    # bcc.b done
    c += bytes.fromhex("0c80") + struct.pack(">I", ymd_packed(LOWER))  # cmpi.l #LOWER,d0
    c += bytes.fromhex("6532")                                    # bcs.b done
    c += bytes.fromhex("2f00")                                    # move.l d0,-(a7)
    c += bytes.fromhex("4eb9") + struct.pack(">I", YMD2MJD)       # jsr ymd->MJD
    c += bytes.fromhex("588f")                                    # addq.l #4,a7
    c += bytes.fromhex("06800000" "1c00")                         # loop: addi.l #7168,d0
    c += bytes.fromhex("0c80") + struct.pack(">I", mjd(pivot))    # cmpi.l #PIVOT_MJD,d0
    c += bytes.fromhex("65f2")                                    # bcs.b loop
    c += bytes.fromhex("2f00")                                    # move.l d0,-(a7)
    c += bytes.fromhex("4eb9") + struct.pack(">I", MJD2YMD)       # jsr MJD->ymd
    c += bytes.fromhex("588f")                                    # addq.l #4,a7
    # HP's MJD->ymd returns Dec 31 of a leap year as "Jan 0 of next year"; fix it up
    c += bytes.fromhex("4a00")                                    # tst.b d0     (day == 0?)
    c += bytes.fromhex("660c")                                    # bne.b done
    c += bytes.fromhex("4240")                                    # clr.w d0
    c += bytes.fromhex("0480" "00010000")                         # subi.l #$10000,d0  (year-1)
    c += bytes.fromhex("0040" "0c1f")                             # ori.w #$0C1F,d0    (12/31)
    c += bytes.fromhex("4e75")                                    # done: rts
    assert len(c) == 0x4A
    return c + b"WNRO-FIX\x00\x00"


def checksum(d, a, b):
    return sum(d[a:b:2]) & 0xFFFF, sum(d[a + 1:b:2]) & 0xFFFF


def store_checksum(d, a, b):
    ev, od = checksum(d, a, b)
    d[b], d[b + 2] = ev >> 8, ev & 0xFF
    d[b + 1], d[b + 3] = od >> 8, od & 0xFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--pivot", default="2026-01-01",
                    help="earliest date considered real (default 2026-01-01, valid until 2045-08-17)")
    ap.add_argument("--split", action="store_true", help="also write even/odd byte EPROM images")
    a = ap.parse_args()
    pivot = datetime.date.fromisoformat(a.pivot)

    d = bytearray(open(a.inp, "rb").read())
    if len(d) != 0x80000 or d[HOOK_ADDR:HOOK_ADDR + len(ORIG_HOOK)] != ORIG_HOOK \
            or d[0x12EEE:0x12EF2] != b"3543":
        sys.exit("input is not an unpatched Z3801A Rev 3543 image")
    code = build_code(pivot)
    if any(x != 0xFF for x in d[NEW_ADDR:NEW_ADDR + len(code)]):
        sys.exit("patch area not empty")

    d[NEW_ADDR:NEW_ADDR + len(code)] = code
    d[HOOK_ADDR:HOOK_ADDR + 6] = bytes.fromhex("4ef9") + struct.pack(">I", NEW_ADDR)  # jmp NEW_ADDR
    for r in CKS_REGIONS:
        store_checksum(d, *r)
    for r in CKS_REGIONS:
        ev, od = checksum(d, *r)
        assert (ev, od) == ((d[r[1]] << 8) | d[r[1] + 2], (d[r[1] + 1] << 8) | d[r[1] + 3])

    open(a.out, "wb").write(d)
    print(f"wrote {a.out}  pivot {pivot}  (dates {LOWER}..{pivot - datetime.timedelta(1)} get +1024 weeks;"
          f" valid until {pivot + datetime.timedelta(7168 - 1)})")
    if a.split:
        base = a.out.rsplit(".", 1)[0]
        open(base + "_EVEN.BIN", "wb").write(d[0::2])
        open(base + "_ODD.BIN", "wb").write(d[1::2])
        print(f"wrote {base}_EVEN.BIN / {base}_ODD.BIN (D15-D8 / D7-D0)")


if __name__ == "__main__":
    main()
