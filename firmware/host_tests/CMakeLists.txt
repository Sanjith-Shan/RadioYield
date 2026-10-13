# Host build of the portable firmware codec + its unit tests.
#   cmake -S firmware/host_tests -B build/fw_host && cmake --build build/fw_host && ctest --test-dir build/fw_host
# Optional sanitizers: -DRY_SANITIZE=ON (ASan + UBSan).
cmake_minimum_required(VERSION 3.16)
project(ry_codec_host_tests C)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
option(RY_SANITIZE "Build with AddressSanitizer + UndefinedBehaviorSanitizer" OFF)
if(RY_SANITIZE)
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
    add_link_options(-fsanitize=address,undefined)
endif()

add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../components/ry_codec ${CMAKE_CURRENT_BINARY_DIR}/ry_codec)

add_executable(test_codec test_codec.c)
target_link_libraries(test_codec PRIVATE ry_codec)
if(CMAKE_C_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(test_codec PRIVATE -Wall -Wextra -Werror)
endif()

enable_testing()
add_test(NAME ry_codec COMMAND test_codec ${CMAKE_CURRENT_SOURCE_DIR}/golden_rx_lines.txt)
