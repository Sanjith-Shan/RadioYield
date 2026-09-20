// Coverage-guided mutation fuzzer for the STDF reader (libFuzzer stand-in).
//
// Apple clang ships without libFuzzer, so this driver provides the loop:
//   * edge coverage from -fsanitize-coverage=trace-pc-guard (callbacks below),
//   * an in-memory corpus seeded from a directory; inputs reaching new edges
//     are kept (and written to <work>/corpus),
//   * byte-level and STDF-record-aware mutations,
//   * ASan + UBSan (-fno-sanitize-recover) in the instrumented core; on a
//     sanitizer report or abort the current input is saved to
//     <work>/crashes/ from a death callback / signal handler before exit.
// Non-STDF-Error C++ exceptions escaping the target are findings as well.
//
// usage: ry_stdf_fuzz --seeds DIR --work DIR --seconds N [--max-len B] [--rng S]
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <random>
#include <string>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size);
extern "C" void __sanitizer_set_death_callback(void (*)(void));

// ------------------------------------------------------------ coverage
static uint32_t g_nguards = 0;
static uint8_t* g_hits = nullptr;

extern "C" void __sanitizer_cov_trace_pc_guard_init(uint32_t* start, uint32_t* stop) {
    if (start == stop || *start) return;
    for (uint32_t* x = start; x < stop; ++x) *x = ++g_nguards;
    uint8_t* h = static_cast<uint8_t*>(std::calloc(g_nguards + 1, 1));
    if (g_hits) {
        std::free(g_hits);  // rare: multiple modules; old counts are discarded
    }
    g_hits = h;
}
extern "C" void __sanitizer_cov_trace_pc_guard(uint32_t* guard) {
    if (*guard && g_hits) g_hits[*guard] = 1;
}

// ------------------------------------------------------------ crash capture
static const uint8_t* g_cur = nullptr;
static size_t g_cur_len = 0;
static std::string g_crash_dir;

static void save_current(const char* tag) {
    if (!g_cur || g_crash_dir.empty()) return;
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < g_cur_len; ++i) h = (h ^ g_cur[i]) * 1099511628211ull;
    char name[512];
    std::snprintf(name, sizeof name, "%s/%s-%016llx.stdf", g_crash_dir.c_str(), tag, (unsigned long long)h);
    if (FILE* f = std::fopen(name, "wb")) {
        std::fwrite(g_cur, 1, g_cur_len, f);
        std::fclose(f);
        std::fprintf(stderr, "\n== saved crashing input: %s (%zu bytes)\n", name, g_cur_len);
    }
}
static void on_death() { save_current("crash"); }
static void on_signal(int sig) {
    save_current("crash");
    std::signal(sig, SIG_DFL);
    std::raise(sig);
}

// ------------------------------------------------------------ mutations
struct Rng {
    std::mt19937_64 g;
    explicit Rng(uint64_t s) : g(s) {}
    size_t below(size_t n) { return n ? size_t(g() % n) : 0; }
    bool coin(int one_in) { return below(size_t(one_in)) == 0; }
};

static const uint8_t kRecTypes[][2] = {{0, 10}, {0, 20},  {1, 10},  {1, 20},  {1, 30},  {1, 40},  {1, 50},
                                       {1, 60}, {1, 62},  {1, 63},  {1, 70},  {1, 80},  {2, 10},  {2, 20},
                                       {2, 30}, {5, 10},  {5, 20},  {10, 30}, {15, 10}, {15, 15}, {15, 20},
                                       {20, 10}, {20, 20}, {50, 10}, {50, 30}, {7, 7},  {180, 1}};

static bool big_endian_of(const std::vector<uint8_t>& d) { return d.size() > 4 && d[4] == 1; }

static std::vector<size_t> record_offsets(const std::vector<uint8_t>& d) {
    std::vector<size_t> offs;
    bool big = big_endian_of(d);
    size_t p = 0;
    while (p + 4 <= d.size() && offs.size() < 100000) {
        offs.push_back(p);
        size_t rl = big ? (size_t(d[p]) << 8 | d[p + 1]) : (size_t(d[p + 1]) << 8 | d[p]);
        p += 4 + rl;
    }
    return offs;
}

static void put_u16(std::vector<uint8_t>& d, size_t at, uint16_t v, bool big) {
    if (at + 2 > d.size()) return;
    if (big) { d[at] = uint8_t(v >> 8); d[at + 1] = uint8_t(v); }
    else { d[at] = uint8_t(v); d[at + 1] = uint8_t(v >> 8); }
}

static void mutate(std::vector<uint8_t>& d, const std::vector<std::vector<uint8_t>>& corpus, Rng& r,
                   size_t max_len) {
    static const uint8_t interesting8[] = {0, 1, 2, 0x7f, 0x80, 0xfe, 0xff, 0x10, 0x40, 0x20};
    static const uint16_t interesting16[] = {0, 1, 2, 3, 4, 0xff, 0x100, 0x7fff, 0x8000, 0xfffe, 0xffff};
    const int n_ops = 1 + int(r.below(6));
    for (int k = 0; k < n_ops; ++k) {
        if (d.empty()) d.push_back(0);
        switch (r.below(13)) {
            case 0: d[r.below(d.size())] ^= uint8_t(1u << r.below(8)); break;
            case 1: d[r.below(d.size())] = uint8_t(r.g()); break;
            case 2: d[r.below(d.size())] = interesting8[r.below(sizeof interesting8)]; break;
            case 3: put_u16(d, r.below(d.size()), interesting16[r.below(11)], r.coin(2)); break;
            case 4: {  // insert random bytes
                size_t at = r.below(d.size() + 1), n = 1 + r.below(32);
                std::vector<uint8_t> ins(n);
                for (auto& b : ins) b = uint8_t(r.g());
                d.insert(d.begin() + long(at), ins.begin(), ins.end());
                break;
            }
            case 5: {  // erase chunk
                size_t at = r.below(d.size()), n = 1 + r.below(std::min<size_t>(64, d.size() - at));
                d.erase(d.begin() + long(at), d.begin() + long(at + n));
                break;
            }
            case 6: {  // duplicate chunk somewhere else
                size_t at = r.below(d.size()), n = 1 + r.below(std::min<size_t>(256, d.size() - at));
                std::vector<uint8_t> chunk(d.begin() + long(at), d.begin() + long(at + n));
                size_t to = r.below(d.size() + 1);
                d.insert(d.begin() + long(to), chunk.begin(), chunk.end());
                break;
            }
            case 7: d.resize(r.below(d.size() + 1)); break;  // truncate
            case 8: {  // splice with another corpus entry
                const auto& o = corpus[r.below(corpus.size())];
                if (o.empty()) break;
                size_t cut = r.below(d.size() + 1), from = r.below(o.size());
                d.resize(cut);
                d.insert(d.end(), o.begin() + long(from), o.end());
                break;
            }
            case 9:
            case 10: {  // record-aware: corrupt REC_LEN of a record
                auto offs = record_offsets(d);
                if (offs.empty()) break;
                size_t p = offs[r.below(offs.size())];
                bool big = big_endian_of(d);
                size_t rl = big ? (size_t(d[p]) << 8 | d[p + 1]) : (size_t(d[p + 1]) << 8 | d[p]);
                long delta = long(r.below(9)) - 4;
                uint16_t nv = r.coin(3) ? interesting16[r.below(11)] : uint16_t(long(rl) + delta);
                put_u16(d, p, nv, big);
                break;
            }
            case 11: {  // record-aware: retype a record (decoder sees a foreign body)
                auto offs = record_offsets(d);
                if (offs.empty()) break;
                size_t p = offs[r.below(offs.size())];
                const auto& t = kRecTypes[r.below(sizeof kRecTypes / 2)];
                d[p + 2] = t[0];
                d[p + 3] = t[1];
                break;
            }
            case 12: {  // record-aware: duplicate or drop a whole record
                auto offs = record_offsets(d);
                if (offs.size() < 2) break;
                size_t i = r.below(offs.size() - 1);
                size_t a = offs[i], b = std::min(offs[i + 1], d.size());
                if (a >= b) break;
                std::vector<uint8_t> rec(d.begin() + long(a), d.begin() + long(b));
                if (r.coin(2)) d.erase(d.begin() + long(a), d.begin() + long(b));
                else d.insert(d.begin() + long(offs[r.below(offs.size())]), rec.begin(), rec.end());
                break;
            }
        }
    }
    if (d.size() > max_len) d.resize(max_len);
}

// ------------------------------------------------------------ main loop
static std::vector<uint8_t> read_file(const std::filesystem::path& p) {
    std::ifstream in(p, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

static size_t new_edges(std::vector<uint8_t>& seen) {
    size_t nw = 0;
    for (uint32_t i = 1; i <= g_nguards; ++i) {
        if (g_hits[i] && !seen[i]) { seen[i] = 1; ++nw; }
        g_hits[i] = 0;
    }
    return nw;
}

int main(int argc, char** argv) {
    namespace fs = std::filesystem;
    std::string seeds, work;
    double seconds = 60;
    size_t max_len = 1 << 16;
    uint64_t rng_seed = 1;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--seeds") seeds = argv[i + 1];
        else if (a == "--work") work = argv[i + 1];
        else if (a == "--seconds") seconds = std::atof(argv[i + 1]);
        else if (a == "--max-len") max_len = size_t(std::atoll(argv[i + 1]));
        else if (a == "--rng") rng_seed = uint64_t(std::atoll(argv[i + 1]));
    }
    if (seeds.empty() || work.empty()) {
        std::fprintf(stderr, "usage: %s --seeds DIR --work DIR --seconds N [--max-len B] [--rng S]\n", argv[0]);
        return 2;
    }
    fs::create_directories(work + "/corpus");
    fs::create_directories(work + "/crashes");
    g_crash_dir = work + "/crashes";
    __sanitizer_set_death_callback(on_death);
    std::signal(SIGSEGV, on_signal);
    std::signal(SIGABRT, on_signal);
    std::signal(SIGBUS, on_signal);
    std::signal(SIGFPE, on_signal);
    std::signal(SIGILL, on_signal);

    std::vector<uint8_t> seen(g_nguards + 1, 0);
    std::vector<std::vector<uint8_t>> corpus;
    auto run = [&](const std::vector<uint8_t>& in) -> size_t {
        g_cur = in.data();
        g_cur_len = in.size();
        try {
            LLVMFuzzerTestOneInput(in.data(), in.size());
        } catch (const std::exception& e) {
            std::fprintf(stderr, "uncaught exception: %s\n", e.what());
            save_current("exception");
        }
        g_cur = nullptr;
        return new_edges(seen);
    };
    // seeds + previously found corpus (resume)
    for (const std::string& dir : {seeds, work + "/corpus"}) {
        if (!fs::is_directory(dir)) continue;
        for (auto& e : fs::directory_iterator(dir)) {
            if (!e.is_regular_file()) continue;
            auto d = read_file(e.path());
            if (d.size() > max_len) d.resize(max_len);
            run(d);
            corpus.push_back(std::move(d));
        }
    }
    if (corpus.empty()) corpus.push_back({2, 0, 0, 10, 2, 4});
    size_t edges = 0;
    for (auto s : seen) edges += s;
    std::printf("seeded: %zu inputs, %zu/%u edges\n", corpus.size(), edges, g_nguards);
    std::fflush(stdout);

    Rng rng(rng_seed);
    const auto t0 = std::chrono::steady_clock::now();
    auto elapsed = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count(); };
    uint64_t execs = 0, exceptions = 0;
    double next_log = 10;
    std::vector<uint8_t> buf;
    while (elapsed() < seconds) {
        // bias toward small inputs and recent finds
        size_t pick = rng.coin(3) ? corpus.size() - 1 - rng.below(std::min<size_t>(corpus.size(), 64))
                                  : rng.below(corpus.size());
        buf = corpus[pick];
        mutate(buf, corpus, rng, max_len);
        size_t nw = run(buf);
        ++execs;
        if (nw) {
            edges += nw;
            corpus.push_back(buf);
            char name[64];
            std::snprintf(name, sizeof name, "/corpus/id-%06zu", corpus.size());
            std::ofstream(work + name, std::ios::binary).write(reinterpret_cast<const char*>(buf.data()),
                                                                long(buf.size()));
        }
        double t = elapsed();
        if (t >= next_log) {
            std::printf("t=%.0fs execs=%llu exec/s=%.0f corpus=%zu edges=%zu/%u\n", t, (unsigned long long)execs,
                        double(execs) / t, corpus.size(), edges, g_nguards);
            std::fflush(stdout);
            next_log += 10;
        }
    }
    size_t crashes = 0;
    for (auto& e : fs::directory_iterator(work + "/crashes")) crashes += e.is_regular_file();
    (void)exceptions;
    std::printf("DONE seconds=%.1f execs=%llu corpus=%zu edges=%zu/%u crash_files=%zu\n", elapsed(),
                (unsigned long long)execs, corpus.size(), edges, g_nguards, crashes);
    return 0;
}
