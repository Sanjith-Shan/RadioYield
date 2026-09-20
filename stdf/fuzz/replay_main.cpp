// Replays files (or every file in a directory) through LLVMFuzzerTestOneInput.
// Used by ctest (fuzz_regressions) to keep every fuzz finding fixed.
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size);

int main(int argc, char** argv) {
    namespace fs = std::filesystem;
    size_t n = 0;
    for (int a = 1; a < argc; ++a) {
        std::vector<fs::path> files;
        fs::path p(argv[a]);
        if (fs::is_directory(p)) {
            for (auto& e : fs::directory_iterator(p))
                if (e.is_regular_file() && e.path().filename().string()[0] != '.') files.push_back(e.path());
        } else {
            files.push_back(p);
        }
        for (auto& f : files) {
            std::ifstream in(f, std::ios::binary);
            std::vector<uint8_t> buf((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
            LLVMFuzzerTestOneInput(buf.data(), buf.size());
            ++n;
        }
    }
    std::printf("replayed %zu inputs OK\n", n);
    return n == 0 ? 1 : 0;
}
