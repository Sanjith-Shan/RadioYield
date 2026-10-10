# ry_codec: portable codec. Under ESP-IDF it is a component; on the host,
# firmware/host_tests adds it with add_subdirectory() and gets a static library.
if(ESP_PLATFORM)
    idf_component_register(SRCS "ry_codec.c" INCLUDE_DIRS "include")
else()
    add_library(ry_codec STATIC ry_codec.c)
    target_include_directories(ry_codec PUBLIC include)
    if(CMAKE_C_COMPILER_ID MATCHES "Clang|GNU")
        target_compile_options(ry_codec PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Werror)
    endif()
endif()
