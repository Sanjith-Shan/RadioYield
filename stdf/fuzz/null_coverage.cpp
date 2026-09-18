// Empty trace-pc-guard callbacks for binaries that link the instrumented core
// but do not do coverage-guided fuzzing (the sanitized replayer).
#include <cstdint>
extern "C" void __sanitizer_cov_trace_pc_guard_init(uint32_t*, uint32_t*) {}
extern "C" void __sanitizer_cov_trace_pc_guard(uint32_t*) {}
