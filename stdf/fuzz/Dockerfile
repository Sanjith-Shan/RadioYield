# Linux toolchain with libFuzzer for the STDF parser fuzz campaign.
# (Apple clang ships no libFuzzer, and its ASan runtime hangs at start-up on
# macOS 26, so the campaign runs here: Docker Desktop's Linux VM, same Mac.)
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clang-18 libclang-rt-18-dev llvm-18 cmake ninja-build ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV CC=clang-18 CXX=clang++-18 ASAN_SYMBOLIZER_PATH=/usr/bin/llvm-symbolizer-18 UBSAN_SYMBOLIZER_PATH=/usr/bin/llvm-symbolizer-18
WORKDIR /src
