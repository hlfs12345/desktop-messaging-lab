cmake_minimum_required(VERSION 3.14)
project(runner LANGUAGES CXX)

# Define the application target. To change its name, change BINARY_NAME in the
# top-level CMakeLists.txt, not the value here, or `flutter run` will no longer
# work.
#
# Any new source files that you add to the application should be added here.
add_executable(${BINARY_NAME} WIN32
  "flutter_window.cpp"
  "main.cpp"
  "utils.cpp"
  "win32_window.cpp"
  "backend_supervisor.cpp"
  "backend_launch.cpp"
  "backend_channel.cpp"
  "file_dialogs.cpp"
  "${FLUTTER_MANAGED_DIR}/generated_plugin_registrant.cc"
  "Runner.rc"
  "runner.exe.manifest"
)

# Apply the standard set of build settings. This can be removed for applications
# that need different build settings.
apply_standard_settings(${BINARY_NAME})
# Release normal usa bundle; desenvolvimento release requer opt-in de build.
set(EDISK_REPO "${CMAKE_SOURCE_DIR}/../..")
execute_process(COMMAND "${EDISK_REPO}/backend/.venv/Scripts/python.exe" -B
  "${EDISK_REPO}/scripts/release_metadata.py" cmake
  --config "${FLUTTER_MANAGED_DIR}/ephemeral/generated_config.cmake"
  --output "${CMAKE_CURRENT_BINARY_DIR}/edisk_profile.cmake"
  RESULT_VARIABLE EDISK_PROFILE_RESULT)
if(NOT EDISK_PROFILE_RESULT EQUAL 0)
  message(FATAL_ERROR "Metadados e perfil Messaging Lab invalidos")
endif()
include("${CMAKE_CURRENT_BINARY_DIR}/edisk_profile.cmake")
if(EDISK_SOURCE)
  target_compile_definitions(${BINARY_NAME} PRIVATE EDISK_SOURCE_BACKEND)
endif()
if(EDISK_CHANNEL STREQUAL "candidato")
  target_compile_definitions(${BINARY_NAME} PRIVATE EDISK_DISTRIBUTION)
endif()
configure_file("${EDISK_REPO}/installer/native/runner_policy.h.in"
  "${CMAKE_CURRENT_BINARY_DIR}/runner_policy.h" @ONLY)
target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}" "${EDISK_REPO}/installer/native")
target_sources(${BINARY_NAME} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/edisk_profile.rc")
target_sources(${BINARY_NAME} PRIVATE "${EDISK_REPO}/installer/native/installation.cpp")
target_compile_options(${BINARY_NAME} PRIVATE /utf-8)

# Add preprocessor definitions for the build version.
target_compile_definitions(${BINARY_NAME} PRIVATE "FLUTTER_VERSION=\"${FLUTTER_VERSION}\"")
target_compile_definitions(${BINARY_NAME} PRIVATE "FLUTTER_VERSION_MAJOR=${FLUTTER_VERSION_MAJOR}")
target_compile_definitions(${BINARY_NAME} PRIVATE "FLUTTER_VERSION_MINOR=${FLUTTER_VERSION_MINOR}")
target_compile_definitions(${BINARY_NAME} PRIVATE "FLUTTER_VERSION_PATCH=${FLUTTER_VERSION_PATCH}")
target_compile_definitions(${BINARY_NAME} PRIVATE "FLUTTER_VERSION_BUILD=${FLUTTER_VERSION_BUILD}")

# Disable Windows macros that collide with C++ standard library functions.
target_compile_definitions(${BINARY_NAME} PRIVATE "NOMINMAX")
target_compile_definitions(${BINARY_NAME} PRIVATE _WIN32_WINNT=0x0A00)

# Add dependency libraries and include directories. Add any application-specific
# dependencies here.
target_link_libraries(${BINARY_NAME} PRIVATE flutter flutter_wrapper_app)
target_link_libraries(${BINARY_NAME} PRIVATE "dwmapi.lib")
target_link_libraries(${BINARY_NAME} PRIVATE bcrypt)
target_link_libraries(${BINARY_NAME} PRIVATE comdlg32)
target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_SOURCE_DIR}")

# Run the Flutter tool portions of the build. This must not be removed.
add_dependencies(${BINARY_NAME} flutter_assemble)
