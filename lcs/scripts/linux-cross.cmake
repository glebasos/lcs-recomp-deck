# Linux x86_64 cross build with clang and lld, for a Windows host without WSL.
# lcs/scripts/fetch_linux_sysroot.py fills out/linux-cross/ (Debian sysroot, glslang, pkgconf).
# lcs/scripts/build_linux_cross.ps1 runs both steps.
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

get_filename_component(_lcs_repo "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(LCS_CROSS_DIR "${_lcs_repo}/out/linux-cross" CACHE PATH "Output of fetch_linux_sysroot.py")
set(LLVM_BIN "C:/Program Files/LLVM/bin" CACHE PATH "clang, lld and llvm-ar")
# try_compile projects see only these variables, not the cache.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES LCS_CROSS_DIR LLVM_BIN)

set(CMAKE_SYSROOT "${LCS_CROSS_DIR}/sysroot")
set(CMAKE_C_COMPILER "${LLVM_BIN}/clang.exe")
set(CMAKE_CXX_COMPILER "${LLVM_BIN}/clang++.exe")
set(CMAKE_AR "${LLVM_BIN}/llvm-ar.exe" CACHE FILEPATH "")
set(CMAKE_RANLIB "${LLVM_BIN}/llvm-ranlib.exe" CACHE FILEPATH "")
set(CMAKE_C_COMPILER_TARGET x86_64-linux-gnu)
set(CMAKE_CXX_COMPILER_TARGET x86_64-linux-gnu)

# Static libstdc++ and libgcc drop the GLIBCXX floor, so only glibc, SDL2, Vulkan
# and FFmpeg have to come from the machine that runs the binary.
set(_lcs_link "-fuse-ld=lld -static-libstdc++ -static-libgcc")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_lcs_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_lcs_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_lcs_link}")
# The static libstdc++ owns __cxa_throw, so lcs_crash.cpp must not interpose it.
set(CMAKE_CXX_FLAGS_INIT "-DLCS_NO_CXA_THROW_HOOK=1")

set(PKG_CONFIG_EXECUTABLE "${LCS_CROSS_DIR}/tools/pkgconf.exe" CACHE FILEPATH "")
# The Windows pkgconf rewrites prefix from the .pc location, which is wrong for a sysroot.
set(PKG_CONFIG_ARGN "--dont-define-prefix" CACHE STRING "")
set(ENV{PKG_CONFIG_SYSROOT_DIR} "${CMAKE_SYSROOT}")
set(ENV{PKG_CONFIG_LIBDIR}
    "${CMAKE_SYSROOT}/usr/lib/x86_64-linux-gnu/pkgconfig;${CMAKE_SYSROOT}/usr/share/pkgconfig")
set(ENV{PKG_CONFIG_PATH} "")
list(APPEND CMAKE_PROGRAM_PATH "${LCS_CROSS_DIR}/tools")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
