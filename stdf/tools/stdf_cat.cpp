// ry_stdf_cat: parse STDF files (or every *.std* file in a directory) and print
// record counts, measurement rows and warnings. Exit code 1 if any file could
// not be parsed at all.
//   ry_stdf_cat [--quiet] [--skip-missing] [--no-records] [--repeat N] PATH...
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include "ry_stdf/stdf.hpp"

int main(int argc, char** argv) {
    namespace fs = std::filesystem;
    bool quiet = false, skip_missing = false;
    int repeat = 1;
    ry::stdf::ParseOptions opt;
    std::vector<fs::path> files;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--quiet") { quiet = true; continue; }
        if (a == "--skip-missing") { skip_missing = true; continue; }
        if (a == "--no-records") { opt.records = false; continue; }
        if (a == "--repeat" && i + 1 < argc) { repeat = std::atoi(argv[++i]); continue; }
        fs::path p(a);
        if (fs::is_directory(p)) {
            for (auto& e : fs::directory_iterator(p)) {
                std::string ext = e.path().extension().string();
                if (e.is_regular_file() && (ext == ".stdf" || ext == ".std")) files.push_back(e.path());
            }
        } else if (fs::exists(p)) {
            files.push_back(p);
        } else if (skip_missing) {
            std::printf("skip: %s does not exist\n", a.c_str());
        } else {
            std::fprintf(stderr, "no such file: %s\n", a.c_str());
            return 1;
        }
    }
    std::sort(files.begin(), files.end());
    int rc = 0;
    for (auto& f : files) {
        try {
            auto t0 = std::chrono::steady_clock::now();
            auto r = ry::stdf::parse_file(f.string(), opt);
            for (int k = 1; k < repeat; ++k) r = ry::stdf::parse_file(f.string(), opt);
            double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            std::printf("%s: %lld bytes, %lld records (%lld unknown), byte order %c, %lld measurement rows, "
                        "%lld warnings%s, %.3f s\n",
                        f.filename().string().c_str(), (long long)r.stats.bytes, (long long)r.stats.records,
                        (long long)r.stats.unknown, r.stats.byte_order, (long long)r.measurements.rows,
                        (long long)r.stats.warnings, r.stats.truncated ? " (TRUNCATED)" : "", dt);
            if (!quiet) {
                for (auto& kv : r.stats.counts) std::printf("  %s %lld\n", kv.first.c_str(), (long long)kv.second);
                for (auto& kv : r.stats.events) std::printf("  event %s %lld\n", kv.first.c_str(), (long long)kv.second);
                for (auto& m : r.stats.warning_messages) std::printf("  warning: %s\n", m.c_str());
            }
        } catch (const std::exception& e) {
            std::fprintf(stderr, "%s: ERROR %s\n", f.string().c_str(), e.what());
            rc = 1;
        }
    }
    return rc;
}
