#!/usr/bin/env python3
"""
gpib_server.py — Prologix-compatible GPIB-over-Ethernet bridge.
"""
from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
from typing import Optional

try:
    import gpib
except ImportError:
    print("gpib_server: linux-gpib Python bindings missing — run install.sh",
          file=sys.stderr)
    sys.exit(2)


VERSION = "gpib_server 0.1  Keysight-82357B linux-gpib 4.3.7 (patched)"

DEFAULTS = {
    "pad": 10,
    "sad": 0,
    "timeout_idx": 12,
    "auto": 0,
    "eoi": 1,
    "eos": 2,
    "read_tmo_ms": 3000,
}

EOS_TERMS = {0: b"\r\n", 1: b"\r", 2: b"\n", 3: b""}

_TIMO_TABLE = [
    (100,   9),
    (300,  10),
    (1000, 11),
    (3000, 12),
    (10000, 13),
    (30000, 14),
]
def _ms_to_timo_enum(ms: int) -> int:
    for threshold, enum_val in _TIMO_TABLE:
        if ms <= threshold:
            return enum_val
    return 15


log = logging.getLogger("gpib_server")


class ClientSession:
    def __init__(self, board: int, peer: str):
        self.board = board
        self.peer = peer
        self.cfg = dict(DEFAULTS)
        self._dev_cache: dict[tuple[int, int], int] = {}

    def _dev(self) -> int:
        key = (self.cfg["pad"], self.cfg["sad"])
        if key not in self._dev_cache:
            sad_param = 0 if self.cfg["sad"] == 0 else self.cfg["sad"]
            h = gpib.dev(self.board, self.cfg["pad"], sad_param,
                         self.cfg["timeout_idx"], self.cfg["eoi"], 0)
            self._dev_cache[key] = h
            log.debug("[%s] opened dev handle %d for PAD %d SAD %d",
                      self.peer, h, self.cfg["pad"], self.cfg["sad"])
        return self._dev_cache[key]

    def close_all(self):
        for h in self._dev_cache.values():
            try: gpib.close(h)
            except Exception: pass
        self._dev_cache.clear()

    def _board_handle(self) -> int:
        for name in (f"gpib{self.board}", "violet", "agilent_usb"):
            try:
                return gpib.find(name)
            except gpib.GpibError:
                continue
        return gpib.dev(self.board, 0, 0, self.cfg["timeout_idx"], 1, 0)

    def _reopen_for_pad_change(self):
        self.close_all()

    def _format_line(self, s: str) -> bytes:
        return (s + "\n").encode("ascii", errors="replace")

    def _write_data(self, payload: bytes) -> Optional[str]:
        eos = EOS_TERMS[self.cfg["eos"]]
        if eos and not payload.endswith(eos):
            payload = payload + eos
        try:
            gpib.write(self._dev(), payload)
            return None
        except gpib.GpibError as e:
            return f"write failed: {e}"

    def _read_data(self, bufsize: int = 4096) -> tuple[bytes, Optional[str]]:
        try:
            data = gpib.read(self._dev(), bufsize)
            return data, None
        except gpib.GpibError as e:
            return b"", f"read failed: {e}"

    def handle_line(self, line: bytes) -> Optional[bytes]:
        text = line.decode("ascii", errors="replace").strip()
        if not text:
            return None

        if text.startswith("++"):
            return self._handle_meta(text[2:])

        err = self._write_data(line)
        if err:
            return self._format_line(f"ERROR {err}")

        if self.cfg["auto"]:
            data, err = self._read_data()
            if err:
                return self._format_line(f"ERROR {err}")
            if not data.endswith(b"\n"):
                data += b"\n"
            return data
        return None

    def _handle_meta(self, cmd: str) -> Optional[bytes]:
        parts = cmd.split()
        if not parts:
            return None
        name, *args = parts

        try:
            if name == "ver":
                return self._format_line(VERSION)

            if name == "addr":
                if not args:
                    sad = self.cfg["sad"]
                    return self._format_line(
                        f"{self.cfg['pad']}" + (f" {sad}" if sad else ""))
                new_pad = int(args[0])
                new_sad = int(args[1]) if len(args) > 1 else 0
                if not 0 <= new_pad <= 30:
                    return self._format_line("ERROR addr: PAD out of range")
                self.cfg["pad"] = new_pad
                self.cfg["sad"] = new_sad
                self._reopen_for_pad_change()
                return None

            if name == "auto":
                if not args:
                    return self._format_line(str(self.cfg["auto"]))
                self.cfg["auto"] = 1 if args[0] in ("1", "on") else 0
                return None

            if name == "eoi":
                if not args:
                    return self._format_line(str(self.cfg["eoi"]))
                self.cfg["eoi"] = 1 if args[0] == "1" else 0
                self._reopen_for_pad_change()
                return None

            if name == "eos":
                if not args:
                    return self._format_line(str(self.cfg["eos"]))
                v = int(args[0])
                if v not in EOS_TERMS:
                    return self._format_line("ERROR eos: 0..3 only")
                self.cfg["eos"] = v
                return None

            if name == "read":
                how = args[0] if args else "eoi"
                if how == "eoi":
                    data, err = self._read_data(65536)
                elif how.isdigit():
                    data, err = self._read_data(int(how))
                else:
                    data, err = self._read_data(65536)
                if err:
                    return self._format_line(f"ERROR {err}")
                if not data.endswith(b"\n"):
                    data += b"\n"
                return data

            if name == "clr":
                try:
                    gpib.clear(self._dev())
                    return None
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR clr: {e}")

            if name == "ifc":
                try:
                    board_h = self._board_handle()
                    gpib.interface_clear(board_h)
                    gpib.close(board_h)
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR ifc: {e}")
                return None

            if name == "loc":
                try:
                    board_h = self._board_handle()
                    gpib.remote_enable(board_h, 0)
                    gpib.close(board_h)
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR loc: {e}")
                return None

            if name == "rst":
                self.close_all()
                self.cfg = dict(DEFAULTS)
                return None

            if name == "spoll":
                pad = int(args[0]) if args else self.cfg["pad"]
                try:
                    saved_pad = self.cfg["pad"]
                    self.cfg["pad"] = pad
                    self._reopen_for_pad_change()
                    stb = gpib.serial_poll(self._dev())
                    self.cfg["pad"] = saved_pad
                    self._reopen_for_pad_change()
                    return self._format_line(str(stb))
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR spoll: {e}")

            if name == "srq":
                try:
                    board_h = self._board_handle()
                    lines = gpib.lines(board_h)
                    gpib.close(board_h)
                    srq = 1 if (lines & 0x1000) else 0
                    return self._format_line(str(srq))
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR srq: {e}")

            if name == "trg":
                pad = int(args[0]) if args else self.cfg["pad"]
                try:
                    saved_pad = self.cfg["pad"]
                    self.cfg["pad"] = pad
                    self._reopen_for_pad_change()
                    gpib.trigger(self._dev())
                    self.cfg["pad"] = saved_pad
                    self._reopen_for_pad_change()
                    return None
                except gpib.GpibError as e:
                    return self._format_line(f"ERROR trg: {e}")

            if name == "read_tmo_ms":
                if not args:
                    return self._format_line(str(self.cfg["read_tmo_ms"]))
                ms = int(args[0])
                self.cfg["read_tmo_ms"] = ms
                self.cfg["timeout_idx"] = _ms_to_timo_enum(ms)
                self.close_all()
                return None

            if name in ("mode", "savecfg", "eot_enable", "eot_char"):
                return None

            return self._format_line(f"ERROR unknown cmd: ++{cmd}")

        except (ValueError, IndexError) as e:
            return self._format_line(f"ERROR bad args: {e}")


BUS_LOCK = threading.Lock()


def client_thread(conn: socket.socket, addr: tuple, board: int):
    peer = f"{addr[0]}:{addr[1]}"
    log.info("[%s] connected", peer)
    session = ClientSession(board, peer)
    conn.settimeout(300)

    buf = b""
    try:
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                log.info("[%s] idle timeout", peer)
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.rstrip(b"\r")
                with BUS_LOCK:
                    reply = session.handle_line(line)
                if reply:
                    conn.sendall(reply)
    except Exception as e:
        log.warning("[%s] error: %s", peer, e)
    finally:
        session.close_all()
        conn.close()
        log.info("[%s] disconnected", peer)


def serve(bind: str, port: int, board: int):
    s = socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET,
                      socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind, port))
    s.listen(8)
    log.info("gpib_server listening on %s:%d (board %d)", bind, port, board)

    running = [True]
    def _stop(*_):
        running[0] = False
        s.close()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        while running[0]:
            try:
                conn, addr = s.accept()
            except OSError:
                break
            t = threading.Thread(target=client_thread, args=(conn, addr, board),
                                 daemon=True)
            t.start()
    finally:
        try:
            s.close()
        except Exception:
            pass
    log.info("gpib_server stopped")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bind", default="0.0.0.0",
                    help="listen address (default 0.0.0.0, use ::/::1 for IPv6)")
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--board", type=int, default=0,
                    help="linux-gpib board index (matches `gpib_config --minor N`)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import os
    dev_path = f"/dev/gpib{args.board}"
    if not os.path.exists(dev_path):
        log.error("%s missing — run `sudo gpib_config --minor %d`?",
                  dev_path, args.board)
        return 1

    serve(args.bind, args.port, args.board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
