#!/usr/bin/env python3
"""
gpib_cold_init — recover the 82357B from its power-on "fault" state.

Usage:
  sudo gpib_cold_init              # idempotent: detach kernel driver,
                                   # reset, replay init, re-attach
  sudo gpib_cold_init --no-rebind  # leave kernel driver detached
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import threading
import time

LOCK_PATH = "/run/gpib_cold_init.lock"

try:
    import usb.core
    import usb.util
except ImportError:
    print("gpib_cold_init: python3-usb (pyusb) is required", file=sys.stderr)
    sys.exit(3)

VID, PID = 0x0957, 0x0718
EP_IN, EP_OUT, EP_INT = 0x82, 0x06, 0x88

INIT_SEQUENCE = [
    ("RD_REGS(0x09)",            "050109"),
    ("WR RESET_TO_POWERUP=0x01", "04010c01"),
    ("WR 19-reg init batch",
        "04130a070b01038000100102030503040415060003950309030a"
        "05000318032003010300030c038f"),
    ("WR AUXCR sweep + PROTOCOL_CONTROL", "04050390030e030f09030d01"),
    ("WR FAST_TALKER_T1=0x27",   "04010e27"),
]


def find_dev():
    return usb.core.find(idVendor=VID, idProduct=PID)


def open_dev(log):
    dev = find_dev()
    if dev is None:
        log.error("no 0957:0718 on the bus")
        sys.exit(2)

    for intf in range(2):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except (usb.core.USBError, NotImplementedError):
            pass

    try:
        dev.reset()
    except usb.core.USBError as e:
        log.warning("dev.reset failed: %s (continuing)", e)
    time.sleep(0.5)
    dev = find_dev()
    if dev is None:
        log.error("device disappeared after reset")
        sys.exit(2)

    for intf in range(2):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except (usb.core.USBError, NotImplementedError):
            pass

    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        log.debug("set_configuration: %s (continuing)", e)
    usb.util.claim_interface(dev, 0)
    return dev


def reattach_kernel_driver(dev, log):
    try:
        usb.util.release_interface(dev, 0)
    except usb.core.USBError:
        pass
    try:
        dev.attach_kernel_driver(0)
    except (usb.core.USBError, NotImplementedError) as e:
        log.debug("attach_kernel_driver: %s", e)


def clear_halts(dev):
    for ep in (EP_IN, EP_OUT, EP_INT):
        for _ in range(2):
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass


def start_int_polling(dev):
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            try:
                dev.read(EP_INT, 8, timeout=3000)
            except usb.core.USBTimeoutError:
                pass
            except usb.core.USBError:
                return

    t = threading.Thread(target=poller, daemon=True)
    t.start()
    time.sleep(0.1)
    return stop, t


def xfer_abort(dev, log):
    try:
        r = dev.ctrl_transfer(0xC0, 0x04, 0x00A0, 0, 2, timeout=1000)
        if bytes(r) != b"\x5f\x00":
            log.warning("xfer_abort unexpected response %s", bytes(r).hex())
    except usb.core.USBError as e:
        log.warning("xfer_abort failed: %s", e)


def bulk_cmd(dev, label, hex_):
    payload = bytes.fromhex(hex_)
    dev.write(EP_OUT, payload, timeout=2000)
    resp = bytes(dev.read(EP_IN, 0x20, timeout=2000))
    if resp[:2] not in (b"\xfa\x00", b"\xfb\x00"):
        raise RuntimeError(f"{label}: unexpected IN {resp[:8].hex()}")


def run_init(dev, log):
    xfer_abort(dev, log)
    for label, hex_ in INIT_SEQUENCE:
        bulk_cmd(dev, label, hex_)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-rebind", action="store_true",
                   help="don't modprobe agilent_82357a afterwards")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")
    log = logging.getLogger("gpib_cold_init")

    lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("another gpib_cold_init is already running — exiting")
        return 0

    if find_dev() is None:
        log.info("no 82357B present — nothing to do")
        return 0

    try:
        driver_path = "/sys/bus/usb/drivers/agilent_82357a"
        if os.path.isdir(driver_path):
            bound = [n for n in os.listdir(driver_path) if ":" in n]
            if bound:
                log.info("agilent_82357a already bound to %s — nothing to do",
                         ",".join(bound))
                return 0
    except OSError:
        pass

    dev = open_dev(log)
    try:
        clear_halts(dev)
        stop, _ = start_int_polling(dev)
        try:
            run_init(dev, log)
        except Exception as e:
            log.error("init sequence rejected: %s", e)
            return 4
        time.sleep(1.0)
        stop.set()
    finally:
        if not args.no_rebind:
            reattach_kernel_driver(dev, log)
        else:
            try:
                usb.util.release_interface(dev, 0)
            except usb.core.USBError:
                pass
        try:
            usb.util.dispose_resources(dev)
        except usb.core.USBError:
            pass

    log.info("firmware recovered; one green READY LED should be lit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
