#!/usr/bin/env python3
"""
Scan a GPIB bus via a remote Prologix-over-TCP server. Sends ``*IDN?`` to
each PAD in the range and prints whatever responds.

Usage:
    tools/gpib_scan_tcp.py <host>[:<port>]
    tools/gpib_scan_tcp.py 192.168.50.33
    tools/gpib_scan_tcp.py 192.168.50.33:1234 --start 1 --end 30 --timeout 2
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


def drain(sock: socket.socket, deadline: float) -> str:
    """Read everything arriving before deadline, return decoded text."""
    sock.settimeout(0.3)
    buf = b""
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            if buf:
                break
    return buf.decode(errors="replace").strip()


def send(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\r\n").encode())


def parse_host(s: str) -> tuple[str, int]:
    if ":" in s:
        host, port = s.rsplit(":", 1)
        return host, int(port)
    return s, 1234


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("host", help="server host[:port], default port 1234")
    p.add_argument("--start", type=int, default=1, help="first PAD (default 1)")
    p.add_argument("--end", type=int, default=30, help="last PAD (default 30)")
    p.add_argument("--timeout", type=float, default=2.0,
                   help="per-PAD client drain in seconds (default 2.0)")
    p.add_argument("--gpib-tmo-ms", type=int, default=300,
                   help="server-side GPIB timeout for no-listener "
                        "PADs, in ms (default 300).  Lower = faster scan.")
    p.add_argument("--query", default="*IDN?",
                   help="SCPI query sent to each PAD (default '*IDN?')")
    args = p.parse_args()

    host, port = parse_host(args.host)

    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        print(f"connect {host}:{port} failed: {e}", file=sys.stderr)
        return 2

    try:
        send(sock, "++ver")
        banner = drain(sock, time.monotonic() + 1.0)
        print(f"server: {banner}" if banner else "(no banner)")

        send(sock, "++auto 1")
        send(sock, "++eoi 1")
        send(sock, "++eos 2")
        send(sock, f"++read_tmo_ms {args.gpib_tmo_ms}")
        drain(sock, time.monotonic() + 0.3)

        print(f"scanning PAD {args.start}..{args.end} via {host}:{port} "
              f"({args.timeout:.1f}s per PAD)...")
        t0 = time.monotonic()
        found: list[tuple[int, str]] = []
        for pad in range(args.start, args.end + 1):
            send(sock, f"++addr {pad}")
            drain(sock, time.monotonic() + 0.05)
            send(sock, args.query)
            resp = drain(sock, time.monotonic() + args.timeout)
            lines = [ln for ln in resp.splitlines()
                     if ln and not ln.startswith("ERROR")]
            if lines:
                print(f"  PAD {pad:2d}: {lines[0][:80]}")
                found.append((pad, lines[0]))
            else:
                print(f"  PAD {pad:2d}: no response")

        dt = time.monotonic() - t0
        print(f"---\nscanned {args.end - args.start + 1} addresses in "
              f"{dt:.1f}s; found {len(found)} instrument(s)")
        for pad, idn in found:
            print(f"  PAD {pad:2d}: {idn}")
        return 0 if found else 1
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main())
