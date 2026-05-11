# 82357B Linux GPIB

Linux install script, driver patches, firmware setup, and tools for the Keysight/Agilent 82357B USB-to-GPIB adapter, with optional GPIB server support.

![Verified working result](docs/assets/working_setup.png)

This image shows the tested system working after applying the patch in this repository.

## Background

This project started because I wanted to use an 82357B USB-GPIB adapter on Linux for long-running use.

The adapter worked well on Windows with Keysight IO Libraries, but Windows was not a good fit for a system that needed to stay running all the time. I tried many times to get the adapter working on Linux, both on an x86 PC and on a Raspberry Pi 5, using different guides and information from the web. But it always failed. The adapter would appear on USB, but the LEDs stayed in a bad state and no GPIB communication worked.

At first, it looked like Linux might be loading the wrong firmware. After USB debugging, it turned out the firmware matched what Windows was using. The real issue was in the Linux driver behavior.

Debugging that problem was slow, but later AI tools helped speed up the work by comparing traces, checking behavior, and helping create the code and patches in this repository. That work led to the patch here, along with `install.sh` and an optional GPIB server setup.

The result is a working Linux setup on my tested systems, where GPIB devices can be scanned and used normally.

A large part of the code in this repository was created or improved with AI help. If you find a bug or need an improvement, you can also use AI to help study the problem, create a fix, and contribute it back. Hopefully this project can keep growing and help more people in the electronics engineering community.

## Why this patch is needed

On the tested Linux systems, the normal Linux initialization path did not bring this 82357B firmware variant into a working GPIB state. The adapter could be detected by USB, but it stayed in a failed state and could not do normal GPIB communication.

The patch in this repository changes the driver behavior so the adapter can initialize correctly and work reliably.

## What this repository provides

- Linux install script
- patched `linux-gpib` build flow
- udev rules for device handling
- firmware fetch and install flow
- GPIB scan tools
- optional TCP GPIB server

## Quick start

```bash
git clone https://github.com/czjtel/82357b-linux-gpib.git
cd 82357b-linux-gpib
sudo ./install.sh
```

Then unplug and replug the adapter, and test it with:

```bash
sudo modprobe agilent_82357a
sudo gpib_config --minor 0
python3 tools/gpib_scan.py
```

## `install.sh` options

Run with:

```bash
sudo ./install.sh [options]
```

Available options:

- `--with-diag`  
  Also build the diagnostic firmware in `firmware/diag/`. This is used for GPIO and LED checking.

- `--with-server`  
  Install and enable the optional GPIB-over-Ethernet server in `server/`. This provides a Prologix-compatible TCP server on port `1234`.

- `--no-fetch`  
  Skip downloading the stock firmware. Use this only if the required firmware file is already present locally.

- `-h`, `--help`  
  Show the built-in help message.

## What `install.sh` does

The script is designed to be rerun safely. It does the following:

1. installs required packages with `apt`
2. downloads and builds `linux-gpib` from source
3. applies the patches from `patches/`
4. builds and installs the Python GPIB bindings
5. updates `/etc/gpib.conf` so board `minor=0` uses `agilent_82357a`
6. fetches and installs the stock firmware
7. installs the udev rules
8. optionally builds diagnostic firmware
9. optionally installs and enables the TCP GPIB server

## Example commands

Basic install:

```bash
sudo ./install.sh
```

Install with diagnostic firmware build:

```bash
sudo ./install.sh --with-diag
```

Install with TCP GPIB server:

```bash
sudo ./install.sh --with-server
```

Install without downloading firmware again:

```bash
sudo ./install.sh --no-fetch
```

Install with both diagnostic firmware and server support:

```bash
sudo ./install.sh --with-diag --with-server
```

## After installation

Typical next steps:

```bash
sudo modprobe agilent_82357a
sudo gpib_config --minor 0
sudo python3 tools/gpib_scan.py
```

If server mode was installed, you can test it from another host with:

```bash
nc <host-ip> 1234
++ver
++addr 10
*IDN?
++read
```

## Notes

- This project does not include Keysight proprietary firmware blobs in the repository.
- Firmware is loaded into RAM only. EEPROM is not changed.
- This is an independent project and is not affiliated with or endorsed by Keysight Technologies.

## Contributing

If you improve it or make it work on more Linux systems, contributions are welcome.

If you hit a problem, AI tools may also help debug logs, traces, and driver behavior. Please share fixes back so the project can keep moving in the electronics engineering community.

## License

Repository code is BSD-2-Clause unless noted otherwise.
