#!/usr/bin/env bash
# install.sh — one-shot deploy of the Keysight 82357B USB-GPIB dongle
# with our repo's patched linux-gpib driver.  Tested on Ubuntu 25.10
# aarch64 / kernel 6.17 on a Raspberry Pi 5; should also work on
# other recent Debian-family systems with minor tweaks.
#
# Does, in order:
#   1. apt-install build deps (fxload, sdcc-optional, python deps, etc.)
#   2. linux-gpib 4.3.7 from SourceForge source (since the upstream
#      apt package doesn't exist on Ubuntu 25.10 and the kernel is too
#      new for older DKMS builds anyway)
#   3. apply the 10-piece driver patch from patches/
#      (docs/14-success.md has the full rationale)
#   4. build + install the Gpib Python bindings from language/python/
#      (not part of `make install` by default, needed by tools/)
#   5. patch /etc/gpib.conf so `minor=0` is board_type="agilent_82357a"
#      (upstream default is "ni_pci" which fails on our USB dongle)
#   6. fetch stock firmware (hash-verified), install to /lib/firmware
#   7. install udev rule so fxload fires on 0957:0518 hotplug
#
# Idempotent: re-running is safe.  The kernel-module build step may
# take a couple of minutes on a Pi; the rest finishes in seconds.
#
# Options:
#   --with-server  install + enable the GPIB-over-Ethernet server
#                  (Prologix-compatible, default port 1234; see server/)
#   --no-fetch     skip firmware download (use an already-placed .hex)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOCK_DIR="${REPO_ROOT}/firmware/stock"
STOCK_HEX="${STOCK_DIR}/downloaded/measat_releaseX1.8.hex"
FW_DST="/lib/firmware/agilent_82357a/measat_releaseX1.8.hex"
UDEV_DST="/etc/udev/rules.d/99-agilent-82357b.rules"

WITH_SERVER=0
NO_FETCH=0
for arg in "$@"; do
    case "$arg" in
        --with-server) WITH_SERVER=1 ;;
        --no-fetch)    NO_FETCH=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install:err]\033[0m %s\n' "$*" >&2; exit 1; }

need_root() {
    [[ $EUID -eq 0 ]] || die "must run as root (try: sudo $0)"
}

detect_hw() {
    log "Detecting 82357B..."
    if lsusb | grep -Eq '0957:0(5|7)18'; then
        log "  found: $(lsusb | grep -E '0957:0(5|7)18')"
    else
        warn "  no 82357B on the USB bus — install will still complete"
    fi
}

apt_install() {
    local pkgs=(
        fxload
        build-essential
        dkms
        git
        curl
        "linux-headers-$(uname -r)"
        python3
        python3-setuptools
        python3-usb
        libusb-1.0-0-dev
    )
    log "Installing apt packages..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    local missing=()
    for p in "${pkgs[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
    done
    if (( ${#missing[@]} )); then
        log "  missing: ${missing[*]}"
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
    else
        log "  all present"
    fi
}

install_linux_gpib() {
    log "Installing linux-gpib (source build, so patches/ apply)..."

    if dpkg -s linux-gpib-dkms >/dev/null 2>&1; then
        warn "  linux-gpib-dkms from apt is present; removing so source build wins"
        DEBIAN_FRONTEND=noninteractive apt-get remove -y linux-gpib-dkms \
            linux-gpib-user 2>/dev/null || true
    fi

    build_linux_gpib_from_source
}

detect_cross_build() {
    if [[ "$(dpkg --print-architecture 2>/dev/null)" == "armhf" ]] && \
       [[ "$(uname -m)" == "aarch64" ]]; then
        if ! command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
            warn "  armhf userspace + aarch64 kernel — installing cross-compiler"
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                gcc-aarch64-linux-gnu crossbuild-essential-arm64 2>/dev/null || \
                DEBIAN_FRONTEND=noninteractive apt-get install -y gcc-aarch64-linux-gnu
        fi
        export ARCH=arm64
        export CROSS_COMPILE=aarch64-linux-gnu-
        log "  cross-compile: ARCH=arm64 CROSS_COMPILE=${CROSS_COMPILE}"
    fi
}

build_linux_gpib_from_source() {
    local version=4.3.7
    local src_dir=/usr/local/src/linux-gpib
    local outer="linux-gpib-${version}.tar.gz"
    local url="https://sourceforge.net/projects/linux-gpib/files/linux-gpib%20for%203.x.x%20and%202.6.x%20kernels/${version}/${outer}/download"

    detect_cross_build

    mkdir -p "$src_dir"
    cd "$src_dir"
    if [[ ! -f "$outer" ]]; then
        log "  fetching $outer"
        curl -fL -o "$outer" "$url" || die "could not download $outer"
    fi
    tar --skip-old-files -xzf "$outer"
    cd "linux-gpib-${version}"

    [[ -f "linux-gpib-kernel-${version}.tar.gz" ]] && \
        tar --skip-old-files -xzf "linux-gpib-kernel-${version}.tar.gz"
    [[ -f "linux-gpib-user-${version}.tar.gz" ]] && \
        tar --skip-old-files -xzf "linux-gpib-user-${version}.tar.gz"

    if [[ -d "${REPO_ROOT}/patches" ]]; then
        log "  applying local patches"
        local p
        for p in "${REPO_ROOT}/patches"/*.patch; do
            [[ -f "$p" ]] || continue
            log "    $(basename "$p")"
            patch -p1 -N --silent < "$p" -d "linux-gpib-kernel-${version}" || \
                warn "    $(basename "$p") did not apply cleanly — maybe already applied"
        done
    fi

    log "  building linux-gpib-kernel (a couple minutes on a Pi 5)"
    cd "linux-gpib-kernel-${version}"
    ./bootstrap 2>/dev/null || true
    if [[ -x configure ]]; then
        ./configure
    fi
    make -j"$(nproc)"
    make install
    depmod -a
    cd ..

    log "  building linux-gpib-user"
    cd "linux-gpib-user-${version}"
    ./bootstrap 2>/dev/null || true
    ./configure --sysconfdir=/etc
    make -j"$(nproc)"
    make install
    ldconfig

    log "  building python-gpib bindings"
    if [[ -d language/python ]]; then
        cd language/python
        python3 setup.py install >/dev/null 2>&1 || \
            warn "    python-gpib bindings did not install cleanly"
        cd ../..
    fi

    cd "$REPO_ROOT"
}

configure_gpib_conf() {
    local conf=/etc/gpib.conf
    [[ -f "$conf" ]] || return 0
    if grep -qE 'board_type *= *"agilent_82357a"' "$conf"; then
        log "  /etc/gpib.conf already set for agilent_82357a"
        return 0
    fi
    sed -i '0,/board_type *= *"ni_pci"/ s//board_type = "agilent_82357a"/' "$conf"
    log "  patched /etc/gpib.conf: minor 0 board_type=agilent_82357a"
}

fetch_stock_fw() {
    if [[ ${NO_FETCH} -eq 1 ]]; then
        log "Skipping stock firmware fetch (--no-fetch)"
        [[ -f "${STOCK_HEX}" ]] || die "${STOCK_HEX} missing — can't install stock FW"
        return 0
    fi
    log "Fetching stock firmware..."
    if [[ -n "${SUDO_USER-}" ]]; then
        sudo -u "${SUDO_USER}" bash "${STOCK_DIR}/fetch.sh"
    else
        bash "${STOCK_DIR}/fetch.sh"
    fi
}

install_stock_fw() {
    install -Dm0644 "${STOCK_HEX}" "${FW_DST}"
    log "  installed stock firmware to ${FW_DST}"
}

install_udev() {
    install -Dm0644 "${REPO_ROOT}/udev/99-agilent-82357b.rules" "${UDEV_DST}"
    install -Dm0644 "${REPO_ROOT}/udev/gpib-82357b-no-autoload.conf" \
        /etc/modprobe.d/gpib-82357b-no-autoload.conf
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=usb
    log "  udev rule + modprobe blacklist installed and reloaded"
}

install_server() {
    if [[ ${WITH_SERVER} -eq 0 ]]; then return 0; fi
    log "Installing GPIB-over-Ethernet server..."

    install -Dm0755 "${REPO_ROOT}/server/gpib_server.py" \
        /usr/local/bin/gpib_server
    install -Dm0755 "${REPO_ROOT}/server/gpib_cold_init.py" \
        /usr/local/bin/gpib_cold_init
    install -Dm0644 "${REPO_ROOT}/server/gpib-server.service" \
        /etc/systemd/system/gpib-server.service
    install -Dm0644 "${REPO_ROOT}/server/gpib-hotplug.service" \
        /etc/systemd/system/gpib-hotplug.service
    install -Dm0644 "${REPO_ROOT}/server/gpib-unload.service" \
        /etc/systemd/system/gpib-unload.service

    systemctl daemon-reload
    systemctl enable gpib-server.service >/dev/null 2>&1 || \
        warn "  systemctl enable gpib-server failed"

    if systemctl is-active --quiet gpib-server.service; then
        log "  restarting already-running gpib-server"
        systemctl restart gpib-server.service || \
            warn "  restart failed; check: journalctl -u gpib-server"
    else
        systemctl start gpib-server.service || \
            warn "  start failed; check: journalctl -u gpib-server"
    fi

    if systemctl is-active --quiet gpib-server.service; then
        log "  gpib-server.service is active on port 1234"
    else
        warn "  gpib-server.service did not reach active state; see journalctl -u gpib-server"
    fi
}

post_summary() {
    cat <<EOF

$(printf '\033[1;32m[install] done.\033[0m')

Next steps:
  1. Plug in the 82357B, or replug it.
  2. Bring the board online:
        sudo modprobe agilent_82357a
        sudo gpib_config --minor 0
  3. The READY LED should go solid green.
  4. Scan the bus for instruments:
        sudo python3 ${REPO_ROOT}/tools/gpib_scan.py
$(if [[ ${WITH_SERVER} -eq 1 ]]; then cat <<EOS2
  5. GPIB-over-Ethernet daemon is enabled and listening on 0.0.0.0:1234.
     Test from another host:
        nc <this-host-ip> 1234
        ++ver
        ++addr 10
        *IDN?
        ++read
     Logs:
        journalctl -u gpib-server -f
EOS2
fi)

Firmware installed at:  ${FW_DST}
udev rule installed at: ${UDEV_DST}
Driver patches applied from: ${REPO_ROOT}/patches/

If the scan returns no instruments but \`lsusb\` shows 0957:0718
and dmesg is clean, check cabling, instrument power, and each
instrument's front-panel I/O configuration.
EOF
}

main() {
    need_root
    detect_hw
    apt_install
    install_linux_gpib
    configure_gpib_conf
    fetch_stock_fw
    install_stock_fw
    install_udev
    install_server
    post_summary
}

main "$@"
