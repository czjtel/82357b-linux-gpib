#!/usr/bin/env python3
"""
gpib_scan.py — walk every primary GPIB address and ask *IDN?. Report any
instrument that responds. Useful for finding an attached DMM / scope /
etc. without assuming its configured PAD.

Usage:
    sudo python3 gpib_scan.py [--board N] [--timeout-s SECS]
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import gpib
except ImportError as e:
    print(f"[scan] missing dep: {e}. Install via linux-gpib's language/python setup.py.", file=sys.stderr)
    sys.exit(2)


def open_device(board: int, pad: int, timeout_idx: int):
    # ibdev(board, pad, sad, timeout, send_eoi, eos)
    #   timeout is linux-gpib's T value (0=never, 11=T1s, 12=T3s, 13=T10s)
    return gpib.dev(board, pad, 0, timeout_idx, 1, 0)


def try_idn(board: int, pad: int, timeout_idx: int = 11) -> str | None:
    """Send *IDN?\n to (board, pad). Returns response string or None on timeout/error."""
    dev = -1
    try:
        dev = open_device(board, pad, timeout_idx)
        gpib.clear(dev)
        gpib.write(dev, b"*IDN?\n")
        resp = gpib.read(dev, 200)
        if not resp:
            return None
        return resp.decode("ascii", errors="replace").strip()
    except gpib.GpibError:
        return None
    finally:
        if dev >= 0:
            try: gpib.close(dev)
            except Exception: pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--board", type=int, default=0)
    p.add_argument("--start", type=int, default=1, help="first primary address (default 1)")
    p.add_argument("--end", type=int, default=30, help="last primary address inclusive (default 30)")
    p.add_argument("--timeout", default="T1s", choices=list(_TIMEOUTS), help="per-address query timeout")
    args = p.parse_args()

    t_idx = _TIMEOUTS[args.timeout]
    found = []
    print(f"Scanning gpib{args.board}, PAD {args.start}..{args.end}, per-address timeout {args.timeout}")
    for pad in range(args.start, args.end + 1):
        t0 = time.time()
        resp = try_idn(args.board, pad, t_idx)
        dt = time.time() - t0
        if resp:
            print(f"  PAD {pad:2d}  RESPONDED ({dt:.2f}s)  → {resp}")
            found.append((pad, resp))
        else:
            print(f"  PAD {pad:2d}  no response      ({dt:.2f}s)", flush=True)

    print()
    if found:
        print(f"Found {len(found)} instrument(s):")
        for pad, s in found:
            print(f"  PAD {pad:2d}  {s}")
        return 0
    else:
        print("No instruments responded. Try: higher timeout, check cable/power, different board.")
        return 1


_TIMEOUTS = {
    "TNONE": 0,
    "T10us": 1,
    "T30us": 2,
    "T100us": 3,
    "T300us": 4,
    "T1ms": 5,
    "T3ms": 6,
    "T10ms": 7,
    "T30ms": 8,
    "T100ms": 9,
    "T300ms": 10,
    "T1s": 11,
    "T3s": 12,
    "T10s": 13,
    "T30s": 14,
    "T100s": 15,
    "T300s": 16,
    "T1000s": 17,
}


if __name__ == "__main__":
    sys.exit(main())
