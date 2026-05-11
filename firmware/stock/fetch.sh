#!/usr/bin/env bash
# fetch.sh — download Keysight's stock 82357B firmware from the linux-gpib
# community mirror (github.com/fmhess/linux_gpib_firmware).
#
# Idempotent. If downloaded/measat_releaseX1.8.hex already exists and its
# sha256 matches expected_sha256.txt, this is a no-op.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="${DIR}/downloaded"
FILE="measat_releaseX1.8.hex"
URL="https://raw.githubusercontent.com/fmhess/linux_gpib_firmware/master/agilent_82357a/${FILE}"
HASHFILE="${DIR}/expected_sha256.txt"

mkdir -p "${DST}"

verify() {
    local path="$1"
    if [[ ! -s "${HASHFILE}" ]]; then
        echo "[stock] no expected_sha256.txt — skipping integrity check"
        return 0
    fi
    local want
    want=$(awk '/^[0-9a-f]{64}/ {print $1; exit}' "${HASHFILE}")
    if [[ -z "${want}" ]]; then
        echo "[stock] no sha256 recorded yet — skipping integrity check"
        return 0
    fi
    local got
    got=$(sha256sum "${path}" | awk '{print $1}')
    if [[ "${want}" != "${got}" ]]; then
        echo "[stock] sha256 mismatch" >&2
        echo "  expected ${want}" >&2
        echo "  got      ${got}"  >&2
        return 1
    fi
    return 0
}

if [[ -f "${DST}/${FILE}" ]] && verify "${DST}/${FILE}" 2>/dev/null; then
    echo "[stock] ${FILE} already present"
    exit 0
fi

echo "[stock] fetching ${URL}"
curl -fL --retry 3 -o "${DST}/${FILE}.part" "${URL}"
mv "${DST}/${FILE}.part" "${DST}/${FILE}"

if ! verify "${DST}/${FILE}"; then
    echo "[stock] downloaded file failed integrity check" >&2
    echo "[stock] if the upstream rolled forward, update expected_sha256.txt" >&2
    sha256sum "${DST}/${FILE}" >&2
    exit 1
fi

echo "[stock] ${FILE} ready at ${DST}/${FILE}"
