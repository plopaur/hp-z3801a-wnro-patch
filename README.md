# HP Z3801A firmware Rev 3543 — GPS week-rollover (WNRO) patch

A small patch for the HP Z3801A GPS-disciplined frequency reference. It corrects
the date the unit reports when its Motorola VP Oncore GPS receiver gives a date
that is 1024 weeks (about 19.6 years) too early.

> **Status:** verified by disassembly, checksum checks and CPU emulation. **Not yet
> tested on real hardware.** Keep your original EPROMs.

## What's in this repository

| File | Description |
|---|---|
| `wnro_patch.py` | Script that builds the patched firmware from your own copy of the original |
| `README.md` | This document |

**No firmware images are included.** The Z3801A firmware is copyrighted by HP,
including the patched versions, which are mostly HP's code. You need to supply
your own copy, read from your unit's EPROMs. `.gitignore` excludes `*.BIN` so
images can't be committed by accident.

### Checksums

Use these to confirm that your dump is the right firmware, and that your
patched build matches the one tested here.

| Image | Description | SHA-256 |
|---|---|---|
| `Z3801A_Rev_3543.BIN` | Original 512 KB image (unmodified) | `29e33b6d85b7371cef16cbf68b071f8a4ca047a8ab391d3c1e8087e553199bed` |
| `Z3801A_Rev_3543_EVEN.BIN` | Original, even-address bytes (D15–D8) | `d383ba2bb02a3354dd94096bbfda38b4bea7590022d8d4e3a2cdbe84fdab4c5b` |
| `Z3801A_Rev_3543_ODD.BIN` | Original, odd-address bytes (D7–D0) | `021f0d8e3d977e6aad633e9190d37b4bc30ec553ee2236dd1a8fb78a66d9b5af` |
| `Z3801A_Rev_3543_WNRO.BIN` | Patched 512 KB image | `27de02c4cb34b77489a15393166104fc750de508ee09680cedebf72fd435c314` |
| `Z3801A_Rev_3543_WNRO_EVEN.BIN` | Patched, even-address bytes (D15–D8) | `6927aa3bd08801f3a524ff5b1e2d086496ce100a88acc98e133f61f88a796163` |
| `Z3801A_Rev_3543_WNRO_ODD.BIN` | Patched, odd-address bytes (D7–D0) | `1f94f38fabf7d745950cc8389707612e2f5d58a0f8b77e80d5523890a3b8123c` |

The patched checksums apply only to a build with the default cutoff date
(2026-01-01). A different `--pivot` gives different files.

## The problem

The Z3801A's GPS receiver is a 6-channel Motorola VP Oncore. It reports the
date (month, day, 16-bit year) in its `@@Ba` status message. GPS transmits only
a 10-bit week number, so the receiver must guess which 1024-week period it is in.
An old receiver can guess wrong and report a date about 19.6 years in the past.

HP's firmware copies the `@@Ba` date straight into its clock. It never checks
whether the year is plausible. After two readings that disagree with the clock
it sets the clock to the GPS date, even if that date is wrong.

What a wrong date affects:

- the reported date (`:PTIME:DATE?`) and the timecode output
- leap-second scheduling, which depends on the date

What it does not affect: the 10 MHz and 1PPS outputs, and the time of day.

## Firmware analysis

| Item | Details |
|---|---|
| CPU | Motorola MC68331 (system integration module at `$FFFA00`) |
| ROM layout | Bootloader at `0x00000–0x0FFFF`; HP application from `0x10000` (its own vector table there) |
| RTOS | Software Components Group pSOS V4.1, plus pForth |
| Version | `3543` (HP date code: 1995, week 43) at `0x12EEE` |
| `@@Ba` handler | `0x4F558`; decodes 68-byte payload into RAM `$100D36` |
| Date converter | `0x110FA`: month/day/year → year/month/day. **All 7 users of the GPS date call it.** |
| Clock sync | `0x43F44`: compares the GPS date/time with the clock and sets the clock (via `0x10B00` → `0x137D8`) after two mismatches |
| Date helpers | `0x10F46` year/month/day → Modified Julian Day (MJD), `0x10E72` MJD → year/month/day, `0x111CC` date → GPS seconds |

### ROM checksums

The bootloader (`0x680–0x744`) adds up two regions before starting the
application:

| Region | Sum stored at |
|---|---|
| `0x10000–0x3FFFB` | `0x3FFFC` |
| `0x40000–0x7FFFB` | `0x7FFFC` |

Each region has two 16-bit sums: one over even-address bytes, one over
odd-address bytes. The 4 stored bytes are laid out as `[even_hi, odd_hi,
even_lo, odd_lo]`. That way each 8-bit EPROM holds its own checksum. If either
region fails, the application does not start. The application has no other ROM
integrity check.

## The patch

Total change: 97 bytes (48 on the even EPROM, 49 on the odd).

1. **Hook:** the first 6 bytes at `0x110FA` become `JMP $61C00`.
2. **New routine** at `0x61C00`, in ROM space that was unused (filled with `0xFF`):

   ```
   061c00  move.l  4(a7),d0          ; m:d:yyyy
           swap    d0                ; -> yyyy:mm:dd (original behaviour)
           cmpi.l  #PIVOT,d0         ; date >= cutoff?  -> done
           bcc     done
           cmpi.l  #1990-01-01,d0    ; earlier than 1990 (receiver has no time yet)? -> done
           bcs     done
           move.l  d0,-(a7)
           jsr     $10F46            ; -> MJD
           addq.l  #4,a7
   loop:   addi.l  #7168,d0          ; +1024 weeks
           cmpi.l  #PIVOT_MJD,d0
           bcs     loop
           move.l  d0,-(a7)
           jsr     $10E72            ; MJD -> yyyy:mm:dd
           addq.l  #4,a7
           tst.b   d0                ; fix HP bug: day 0 means Dec 31 of a leap year
           bne     done
           clr.w   d0
           subi.l  #$10000,d0        ; year - 1
           ori.w   #$0C1F,d0         ; 12/31
   done:   rts
   ```

   The routine is followed by the marker string `WNRO-FIX`.
3. **Checksums:** both regions' checksums are recalculated. The hook is in
   region 1 and the new routine in region 2.

The routine uses only registers d0, d1, a0 and a1, which callers do not expect
to be kept. That matches the compiler's calling convention, so callers are
unaffected.

### Cutoff date

- Dates from 1990-01-01 up to the day before the cutoff get +1024 weeks, repeated
  until they reach the cutoff.
- Dates on or after the cutoff are left unchanged.
- Dates before 1990 are left unchanged. The receiver reports these before it
  knows the time.

With the default cutoff of **2026-01-01**, reported dates are correct until
**2045-08-16**.

### Bug in HP's own code

HP's MJD → date routine (`0x10E72`) returns December 31 of every leap year as
"January 0" of the next year. The patch corrects this for its own result only.
HP's two original call sites still have the bug.

## Building

The script needs only Python 3, with no extra packages. It takes the merged
512 KB image as input.

If you read the two EPROMs separately, combine the dumps first. Save the
even-address chip (D15–D8) as `Z3801A_Rev_3543_EVEN.BIN` and the odd-address
chip (D7–D0) as `Z3801A_Rev_3543_ODD.BIN`, then:

```bash
python3 -c "e=open('Z3801A_Rev_3543_EVEN.BIN','rb').read(); o=open('Z3801A_Rev_3543_ODD.BIN','rb').read(); open('Z3801A_Rev_3543.BIN','wb').write(bytes(b for p in zip(e,o) for b in p))"
```

```bash
shasum -a 256 Z3801A_Rev_3543.BIN
```

The result must match the original 512 KB checksum above. If it doesn't, try
swapping the two dumps. If neither order matches, your unit has different
firmware and this patch does not apply. Then build the patched images:

```bash
python3 wnro_patch.py Z3801A_Rev_3543.BIN Z3801A_Rev_3543_WNRO.BIN --pivot 2026-01-01 --split
```

- `--pivot YYYY-MM-DD` sets the cutoff date.
- `--split` also writes the `_EVEN` and `_ODD` EPROM images.

The script checks that the input is an unpatched Rev 3543 image and that the
patch area is empty. After patching, it confirms that both checksums are valid.

## Verification done

- Disassembled the hook and new routine to confirm the instructions are as intended.
- Both ROM checksum regions match their stored sums.
- Ran the patched `0x110FA` in a 68020 emulator (unicorn), together with the
  original firmware routines:
  - every day from 1990-01-01 to 2045-12-31: 0 errors
  - registers that callers expect to be kept are unchanged, and the stack is balanced
- Recombining the even and odd files byte by byte gives back the merged images
  exactly.

## Installing

1. Read your two original EPROMs and save the images.
2. Compare each dump's SHA-256 with the original EVEN and ODD checksums above.
   Each chip should match one of them exactly. That tells you which socket is
   even and which is odd. If neither matches, the board uses a different
   layout; do not continue.
3. Build the patched images as described under [Building](#building) and check
   them against the patched checksums.
4. Program 27C020-compatible EPROMs with `Z3801A_Rev_3543_WNRO_EVEN.BIN` and
   `Z3801A_Rev_3543_WNRO_ODD.BIN`. Each goes in the socket of the original chip
   with the same name.
5. Power up. If the unit does not start, a checksum failed: check the chip
   placement and programming.
6. Once the unit is tracking satellites, check `:PTIME:DATE?` and `:PTIME:TIME?`.

To go back to the original firmware, refit the original EPROMs.

## Limitations

- **Not proven on hardware.** It is unknown exactly how this unit's Oncore
  reports dates after a rollover. The patch handles any date shifted back by a
  whole number of 1024-week periods, as long as it falls in 1990 or later.
- The patch changes only how the HP firmware reads the date. It cannot fix
  rollover problems inside the Oncore's own firmware, such as trouble
  acquiring satellites.
- The patched firmware still reports version `3543`. The changes are marked
  only by the `WNRO-FIX` string at `0x61C4A`.
- With the default cutoff, reported dates will be wrong again from 2045-08-17.
  Rebuild with a later `--pivot` before then.

## License

Copyright (c) 2026 Philipp Lopaur.

The patch script and this documentation are released under the
[MIT License](LICENSE).

The license does not cover the Z3801A firmware itself, which remains
copyrighted by HP and its successors. That includes images built with
`wnro_patch.py`.
