# TotalMix OSC agent — portable native core + platform daemon.
#
# The `tmosc_core` library is freestanding C99: no platform headers, no third-
# party deps. It builds identically on Windows (MSVC/MinGW), macOS (clang),
# Linux/Raspberry Pi (gcc), and is the base a microcontroller port would reuse.
#
# The Linux daemon (agent/linux) adds ALSA MIDI in + a minimal HTTP client and
# is built only on Linux when libasound is present.
#
#   cmake -S agent -B agent/build && cmake --build agent/build && ctest --test-dir agent/build
cmake_minimum_required(VERSION 3.10)
project(tmosc_agent C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# --- portable core (the base) ---------------------------------------------
add_library(tmosc_core STATIC
  core/tmosc_midi.c
  core/tmosc_match.c
  core/tmosc_clock.c
  core/tmosc_proto.c
  core/tmosc_bindings.c
)
target_include_directories(tmosc_core PUBLIC core)

if(MSVC)
  target_compile_options(tmosc_core PRIVATE /W4)
else()
  target_compile_options(tmosc_core PRIVATE -Wall -Wextra -pedantic)
endif()

# --- tests ----------------------------------------------------------------
enable_testing()
add_executable(test_core tests/test_core.c)
target_link_libraries(test_core PRIVATE tmosc_core)
if(NOT MSVC)
  target_link_libraries(test_core PRIVATE m)   # libm for fabs
endif()
add_test(NAME core COMMAND test_core)

# --- Linux daemon ---------------------------------------------------------
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  find_library(ALSA_LIB asound)
  find_path(ALSA_INC alsa/asoundlib.h)
  if(ALSA_LIB AND ALSA_INC)
    add_executable(tmosc-agent
      linux/main.c
      linux/net.c
      linux/midi_alsa.c
    )
    target_include_directories(tmosc-agent PRIVATE ${ALSA_INC})
    target_link_libraries(tmosc-agent PRIVATE tmosc_core ${ALSA_LIB})
    target_compile_options(tmosc-agent PRIVATE -Wall -Wextra)
    install(TARGETS tmosc-agent RUNTIME DESTINATION bin)
  else()
    message(STATUS "libasound not found — skipping tmosc-agent daemon (core still builds)")
  endif()
endif()
