# TotalMix OSC agent — portable native core + shared runner + platform daemons.
#
# tmosc_core   : freestanding C99, zero deps (the base a microcontroller reuses)
# runner + net : shared, portable (POSIX sockets / Winsock)
# platform     : Linux (ALSA) or Windows (WinMM) MIDI backend + entry point
#
#   cmake -S agent -B agent/build && cmake --build agent/build && ctest --test-dir agent/build
cmake_minimum_required(VERSION 3.15)   # 3.15 => CMP0091 NEW, for CMAKE_MSVC_RUNTIME_LIBRARY
project(tmosc_agent C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
if(MSVC)
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS _WINSOCK_DEPRECATED_NO_WARNINGS)
  # Static CRT: the exes then run on a clean Windows box with NO Visual C++
  # redistributable (no vcruntime140.dll dependency) — copy-and-run.
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
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
  target_link_libraries(test_core PRIVATE m)
endif()
add_test(NAME core COMMAND test_core)

# --- shared runner + net (built into each daemon target) ------------------
set(TMOSC_SHARED_SRC ${CMAKE_CURRENT_SOURCE_DIR}/runner.c ${CMAKE_CURRENT_SOURCE_DIR}/net/net.c)
set(TMOSC_SHARED_INC ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/net)

# --- Linux daemon ---------------------------------------------------------
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  find_library(ALSA_LIB asound)
  find_path(ALSA_INC alsa/asoundlib.h)
  if(ALSA_LIB AND ALSA_INC)
    add_executable(tmosc-agent linux/main.c linux/midi_alsa.c ${TMOSC_SHARED_SRC})
    target_include_directories(tmosc-agent PRIVATE ${TMOSC_SHARED_INC} linux ${ALSA_INC})
    target_link_libraries(tmosc-agent PRIVATE tmosc_core ${ALSA_LIB})
    target_compile_options(tmosc-agent PRIVATE -Wall -Wextra)
    install(TARGETS tmosc-agent RUNTIME DESTINATION bin)
  else()
    message(STATUS "libasound not found — skipping tmosc-agent daemon (core still builds)")
  endif()
endif()

# --- Windows: console daemon + tray GUI (share the same runner) -----------
if(WIN32)
  enable_language(RC)   # compile windows/tray.rc -> embeds tray.ico as the app/tray icon

  add_executable(tmosc-agent windows/main_win.c windows/midi_win.c windows/tray.rc ${TMOSC_SHARED_SRC})
  target_include_directories(tmosc-agent PRIVATE ${TMOSC_SHARED_INC} windows)
  target_link_libraries(tmosc-agent PRIVATE tmosc_core winmm ws2_32)
  install(TARGETS tmosc-agent RUNTIME DESTINATION bin)

  # WIN32 = GUI subsystem (WinMain, no console window)
  add_executable(tmosc-agent-tray WIN32 windows/tray_win.c windows/midi_win.c windows/tray.rc ${TMOSC_SHARED_SRC})
  target_include_directories(tmosc-agent-tray PRIVATE ${TMOSC_SHARED_INC} windows)
  target_link_libraries(tmosc-agent-tray PRIVATE tmosc_core winmm ws2_32 shell32 user32 advapi32)
  install(TARGETS tmosc-agent-tray RUNTIME DESTINATION bin)
endif()
