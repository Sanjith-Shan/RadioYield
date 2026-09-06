cmake_minimum_required(VERSION 3.18)
project(radioyield_stdf LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(RY_STDF_PYTHON "Build the pybind11 module radioyield/stdf/_stdf" ON)
option(RY_STDF_FUZZ "Build the sanitizer + coverage-guided mutation fuzzer (clang/gcc)" OFF)
option(RY_STDF_LIBFUZZER "Build a libFuzzer target (needs a clang with -fsanitize=fuzzer)" OFF)

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  set(RY_WARN -Wall -Wextra -Wno-unused-parameter)
endif()

set(RY_CORE_SOURCES src/records.cpp src/reader.cpp src/writer.cpp)

# ------------------------------------------------------------------ core
add_library(ry_stdf_core STATIC ${RY_CORE_SOURCES})
target_include_directories(ry_stdf_core PUBLIC include PRIVATE src)
target_compile_options(ry_stdf_core PRIVATE ${RY_WARN})
set_target_properties(ry_stdf_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

# ------------------------------------------------------------------ python module
if(RY_STDF_PYTHON)
  set(Python_FIND_VIRTUALENV FIRST)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
  if(NOT pybind11_DIR)
    execute_process(
      COMMAND "${Python_EXECUTABLE}" -m pybind11 --cmakedir
      OUTPUT_VARIABLE pybind11_DIR OUTPUT_STRIP_TRAILING_WHITESPACE
      RESULT_VARIABLE _pb_rc)
    if(NOT _pb_rc EQUAL 0)
      message(FATAL_ERROR "pybind11 not importable by ${Python_EXECUTABLE}; pip install pybind11")
    endif()
  endif()
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_stdf python/module.cpp)
  target_link_libraries(_stdf PRIVATE ry_stdf_core)
  target_compile_options(_stdf PRIVATE ${RY_WARN})
  set(RY_PY_OUT "${CMAKE_CURRENT_SOURCE_DIR}/../radioyield/stdf")
  set_target_properties(_stdf PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${RY_PY_OUT}")
  foreach(cfg Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER ${cfg} CFG)
    set_target_properties(_stdf PROPERTIES LIBRARY_OUTPUT_DIRECTORY_${CFG} "${RY_PY_OUT}")
  endforeach()
endif()

# ------------------------------------------------------------------ tools + tests
add_executable(ry_stdf_cat tools/stdf_cat.cpp)
target_link_libraries(ry_stdf_cat PRIVATE ry_stdf_core)
target_compile_options(ry_stdf_cat PRIVATE ${RY_WARN})

add_executable(ry_stdf_tests tests/test_core.cpp)
target_link_libraries(ry_stdf_tests PRIVATE ry_stdf_core)
target_include_directories(ry_stdf_tests PRIVATE src)
target_compile_options(ry_stdf_tests PRIVATE ${RY_WARN})

# replays every file in fuzz/regressions through the fuzz entry point
add_executable(ry_stdf_replay fuzz/fuzz_target.cpp fuzz/replay_main.cpp)
target_link_libraries(ry_stdf_replay PRIVATE ry_stdf_core)

enable_testing()
add_test(NAME core_unit COMMAND ry_stdf_tests)
add_test(NAME fuzz_regressions
         COMMAND ry_stdf_replay "${CMAKE_CURRENT_SOURCE_DIR}/fuzz/regressions")
set(RY_SAMPLES "${CMAKE_CURRENT_SOURCE_DIR}/../data/raw/stdf_samples")
add_test(NAME public_samples COMMAND ry_stdf_cat --quiet --skip-missing "${RY_SAMPLES}")

# ------------------------------------------------------------------ fuzzing
# Mutation fuzzer with ASan + UBSan and trace-pc-guard edge coverage. The core
# is recompiled with instrumentation; the driver itself is not instrumented
# (it implements the coverage callbacks).
if(RY_STDF_FUZZ)
  set(RY_SAN -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer -g)
  add_library(ry_stdf_core_fuzz STATIC ${RY_CORE_SOURCES} fuzz/fuzz_target.cpp)
  target_include_directories(ry_stdf_core_fuzz PUBLIC include PRIVATE src)
  target_compile_options(ry_stdf_core_fuzz PRIVATE ${RY_SAN} -fsanitize-coverage=trace-pc-guard)
  add_executable(ry_stdf_fuzz fuzz/mutate_driver.cpp)
  target_link_libraries(ry_stdf_fuzz PRIVATE ry_stdf_core_fuzz)
  target_compile_options(ry_stdf_fuzz PRIVATE ${RY_SAN})
  target_link_options(ry_stdf_fuzz PRIVATE -fsanitize=address,undefined)

  add_executable(ry_stdf_replay_san fuzz/replay_main.cpp)
  target_link_libraries(ry_stdf_replay_san PRIVATE ry_stdf_core_fuzz)
  target_compile_options(ry_stdf_replay_san PRIVATE ${RY_SAN})
  target_link_options(ry_stdf_replay_san PRIVATE -fsanitize=address,undefined)
  target_sources(ry_stdf_replay_san PRIVATE fuzz/null_coverage.cpp)
  add_test(NAME fuzz_regressions_sanitized
           COMMAND ry_stdf_replay_san "${CMAKE_CURRENT_SOURCE_DIR}/fuzz/regressions")
endif()

if(RY_STDF_LIBFUZZER)
  add_executable(ry_stdf_libfuzzer fuzz/fuzz_target.cpp ${RY_CORE_SOURCES})
  target_include_directories(ry_stdf_libfuzzer PRIVATE include src)
  target_compile_options(ry_stdf_libfuzzer PRIVATE -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=all -g)
  target_link_options(ry_stdf_libfuzzer PRIVATE -fsanitize=fuzzer,address,undefined)
endif()
