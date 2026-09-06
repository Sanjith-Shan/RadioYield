// Bounds-checked little/big-endian field access shared by reader and writer.
#pragma once
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace ry::stdf::detail {

inline uint16_t bswap(uint16_t v) { return uint16_t((v >> 8) | (v << 8)); }
inline uint32_t bswap(uint32_t v) {
    return (v >> 24) | ((v >> 8) & 0xFF00u) | ((v << 8) & 0xFF0000u) | (v << 24);
}
inline uint64_t bswap(uint64_t v) {
    return (uint64_t(bswap(uint32_t(v))) << 32) | bswap(uint32_t(v >> 32));
}

template <class T>
inline T load(const uint8_t* p, bool swap) {
    static_assert(std::is_arithmetic<T>::value, "arithmetic only");
    if constexpr (sizeof(T) == 1) {
        T v;
        std::memcpy(&v, p, 1);
        return v;
    } else {
        using U = std::conditional_t<sizeof(T) == 2, uint16_t,
                                     std::conditional_t<sizeof(T) == 4, uint32_t, uint64_t>>;
        U u;
        std::memcpy(&u, p, sizeof(U));
        if (swap) u = bswap(u);
        T v;
        std::memcpy(&v, &u, sizeof(T));
        return v;
    }
}

template <class T>
inline void store(uint8_t* p, T v, bool swap) {
    if constexpr (sizeof(T) == 1) {
        std::memcpy(p, &v, 1);
    } else {
        using U = std::conditional_t<sizeof(T) == 2, uint16_t,
                                     std::conditional_t<sizeof(T) == 4, uint32_t, uint64_t>>;
        U u;
        std::memcpy(&u, &v, sizeof(U));
        if (swap) u = bswap(u);
        std::memcpy(p, &u, sizeof(U));
    }
}

inline bool host_is_little() {
    const uint16_t x = 1;
    uint8_t b;
    std::memcpy(&b, &x, 1);
    return b == 1;
}

// Cursor over one record body. Every read is bounds checked; a read that does
// not fit marks the cursor exhausted ("record ended early") and returns false.
struct Cursor {
    const uint8_t* p;
    const uint8_t* e;
    bool swap;
    bool overrun = false;  // a field started but did not fit (vs. clean end)

    size_t left() const { return size_t(e - p); }
    bool at_end() const { return p >= e; }
    void kill() {
        if (p < e) overrun = true;
        p = e;
    }

    template <class T>
    bool get(T& out) {
        if (left() < sizeof(T)) {
            kill();
            return false;
        }
        out = load<T>(p, swap);
        p += sizeof(T);
        return true;
    }
    // Cn: U1 length + bytes
    bool cn(const uint8_t*& s, size_t& n) {
        if (left() < 1) return false;
        size_t len = *p;
        if (left() < 1 + len) {
            kill();
            return false;
        }
        s = p + 1;
        n = len;
        p += 1 + len;
        return true;
    }
    // Bn: U1 byte count + bytes
    bool bn(const uint8_t*& s, size_t& n) { return cn(s, n); }
    // Dn: U2 bit count + ceil(bits/8) bytes
    bool dn(const uint8_t*& s, size_t& n) {
        uint16_t bits;
        if (left() < 2) {
            kill();
            return false;
        }
        bits = load<uint16_t>(p, swap);
        size_t len = (size_t(bits) + 7) / 8;
        if (left() < 2 + len) {
            kill();
            return false;
        }
        s = p + 2;
        n = len;
        p += 2 + len;
        return true;
    }
};

}  // namespace ry::stdf::detail
