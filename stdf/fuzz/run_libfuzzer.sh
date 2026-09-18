#!/usr/bin/env bash
# 30-minute libFuzzer + ASan + UBSan campaign on the STDF reader, in Docker.
# Fork mode with -ignore_crashes so one finding does not end the campaign;
# every crash input lands in build/fuzz/$OUT/artifacts/.
#   stdf/fuzz/run_libfuzzer.sh [SECONDS] [OUTNAME]   (defaults 1800, libfuzzer)
# Needs: docker, seeds from `python stdf/fuzz/make_seeds.py` in build/fuzz/seeds.
# Output: build/fuzz/<OUTNAME>/{log.txt,corpus/,artifacts/}; an existing corpus there is resumed.
set -euo pipefail
SECS="${1:-1800}"
OUT="${2:-libfuzzer}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
docker build -q -t ry-stdf-fuzz "$ROOT/stdf/fuzz" >/dev/null
mkdir -p "$ROOT/build/fuzz/$OUT/corpus" "$ROOT/build/fuzz/$OUT/artifacts"
docker run --rm -v "$ROOT:/src" -w /src ry-stdf-fuzz bash -c "
  set -e
  cmake -S stdf -B build/stdf-fuzz-linux -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DRY_STDF_PYTHON=OFF -DRY_STDF_LIBFUZZER=ON -DRY_STDF_FUZZ=ON >/dev/null
  cmake --build build/stdf-fuzz-linux >/dev/null
  ctest --test-dir build/stdf-fuzz-linux --output-on-failure
  clang++-18 --version | head -1
  nproc
  ./build/stdf-fuzz-linux/ry_stdf_libfuzzer \
      -max_total_time=$SECS -max_len=65536 -timeout=10 -rss_limit_mb=4096 \
      -fork=1 -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1 \
      -print_final_stats=1 -artifact_prefix=build/fuzz/$OUT/artifacts/ \
      build/fuzz/$OUT/corpus build/fuzz/seeds
" 2>&1 | tee "$ROOT/build/fuzz/$OUT/log.txt"
