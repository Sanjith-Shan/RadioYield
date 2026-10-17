#!/usr/bin/env bash
# Build DUT and REF images with an ESP-IDF v5.5.x environment already exported
# (. $IDF_PATH/export.sh), or inside the espressif/idf:v5.5.6 container (CI).
#   firmware/build_all.sh [evt1|evt2] [uart]
# Outputs build/<role>_<variant>[_uart]/ and merged images (flash at 0x0) in build/dist/.
set -euo pipefail
cd "$(dirname "$0")"
variant="${1:-evt1}"
suffix=""
extra=""
if [[ "${2:-}" == "uart" ]]; then suffix="_uart"; extra=";sdkconfig.defaults.uart"; fi
mkdir -p build/dist
for role in dut ref; do
  # the TX-table variant only changes the DUT's behaviour; both get the label so INFO is honest
  bdir="build/${role}_${variant}${suffix}"
  idf.py -B "$bdir" -DIDF_TARGET=esp32c6 -DSDKCONFIG="$bdir/sdkconfig" \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.${role};sdkconfig.defaults.${variant}${extra}" \
    build
  out="$PWD/build/dist/radioyield_${role}_${variant}${suffix}.bin"
  (cd "$bdir" && python -m esptool --chip esp32c6 merge_bin -o "$out" @flash_args)
done
ls -l build/dist
echo "flash: python -m esptool --chip esp32c6 -p PORT -b 460800 write_flash 0x0 build/dist/radioyield_dut_${variant}${suffix}.bin"
