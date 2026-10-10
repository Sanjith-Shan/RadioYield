# RadioYield rig firmware (ESP-IDF >= v5.5, target esp32c6). Role is chosen with an
# sdkconfig.defaults overlay; see PROTOCOL.md and docs/RIG.md. Example:
#   ./build_all.sh evt1        (wraps, per role:)
#   idf.py -B build/dut_evt1 -DIDF_TARGET=esp32c6 -DSDKCONFIG=build/dut_evt1/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.dut;sdkconfig.defaults.evt1" build
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(radioyield_fw)
