# bambu_cli/CMakeLists.txt
# Pure CLI slicer target — links libslic3r only, no wxWidgets.
#
# Integration: Copy this file + cli_main.cpp into the BambuStudio source root,
# then build with:
#   cmake -B build -S . -DSLIC3R_BUILD_CLI=ON
#   cmake --build build --target bambu_cli

cmake_minimum_required(VERSION 3.16)

# ---- Build target: bambu_cli ----
if(DEFINED SLIC3R_BUILD_CLI AND SLIC3R_BUILD_CLI)
    message(STATUS "bambu_cli: Building pure-CLI slicer (no GUI)")

    add_executable(bambu_cli
        bambu_cli/cli_main.cpp
    )

    # Link ONLY libslic3r (the core library — no wx)
    target_link_libraries(bambu_cli
        PRIVATE libslic3r
    )

    # Use the same include directories as libslic3r
    # (Inherited from the parent CMakeLists.txt)
    target_include_directories(bambu_cli PRIVATE
        ${CMAKE_SOURCE_DIR}/src
    )

    # Same compiler flags as libslic3r (C++17, warnings, optimizations)
    target_compile_features(bambu_cli PRIVATE cxx_std_17)

    if(MSVC)
        target_compile_options(bambu_cli PRIVATE /W3)
    else()
        target_compile_options(bambu_cli PRIVATE
            -Wall -Wextra -Wno-unused-parameter -O2
        )
    endif()

    # Link Boost (same libs libslic3r needs)
    find_package(Boost REQUIRED
        COMPONENTS filesystem system thread locale iostreams
    )
    target_link_libraries(bambu_cli PRIVATE Boost::filesystem Boost::system)

    # Install
    install(TARGETS bambu_cli RUNTIME DESTINATION bin)

    message(STATUS "bambu_cli: configured successfully")
endif()
