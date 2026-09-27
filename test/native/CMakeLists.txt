cmake_minimum_required(VERSION 3.24)
project(fonix_native_tests LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CTest)
enable_testing()

if(NOT APPLE AND NOT UNIX)
  message(FATAL_ERROR "The Phase 0-1 native loader tests currently cover POSIX hosts only")
endif()

option(FONIX_TEST_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(FONIX_TEST_THREAD_SANITIZER "Enable ThreadSanitizer" OFF)

if(FONIX_TEST_SANITIZERS AND FONIX_TEST_THREAD_SANITIZER)
  message(FATAL_ERROR "ThreadSanitizer cannot be combined with AddressSanitizer/UndefinedBehaviorSanitizer")
endif()
if(FONIX_TEST_THREAD_SANITIZER AND (NOT UNIX OR MSVC))
  message(FATAL_ERROR "ThreadSanitizer native tests require a POSIX toolchain")
endif()

# Clang leaves sanitizer symbols unresolved in shared libraries unless the
# shared runtime is selected. Keep --no-undefined and bind every test process
# and shim to the same compiler-owned sanitizer runtime.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND CMAKE_C_COMPILER_ID STREQUAL "Clang"
   AND (FONIX_TEST_SANITIZERS OR FONIX_TEST_THREAD_SANITIZER))
  execute_process(
    COMMAND "${CMAKE_C_COMPILER}" --print-runtime-dir
    OUTPUT_VARIABLE _fonix_sanitizer_runtime_directory
    OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY
  )
  add_link_options(
    -shared-libsan
    "-Wl,-rpath,${_fonix_sanitizer_runtime_directory}"
  )
endif()

find_package(Threads REQUIRED)

set(FONIX_RUNTIME_PROFILE external CACHE STRING "" FORCE)
set(FONIX_SHIM_LIBRARY_TYPE SHARED CACHE STRING "" FORCE)
set(FONIX_SHIM_BUILD_ID native-tests CACHE STRING "" FORCE)
set(
  ONNXRUNTIME_INCLUDE_DIR
  "${CMAKE_CURRENT_LIST_DIR}/../../third_party/onnxruntime/include"
  CACHE PATH "" FORCE
)
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../src" fonix_shim_build)
target_compile_definitions(
  fonix_shim PRIVATE
  FONIX_DESKTOP_PROVIDER_TESTING=1
  FONIX_ALLOCATION_TESTING=1
)

function(fonix_strict_target target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /WX)
  else()
    target_compile_options(
      ${target} PRIVATE
      -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror
    )
    if(FONIX_TEST_SANITIZERS)
      target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
      target_link_options(${target} PRIVATE -fsanitize=address,undefined)
    elseif(FONIX_TEST_THREAD_SANITIZER)
      target_compile_options(${target} PRIVATE -fsanitize=thread -fno-omit-frame-pointer)
      target_link_options(${target} PRIVATE -fsanitize=thread)
    endif()
  endif()
endfunction()

if(FONIX_TEST_SANITIZERS AND NOT MSVC)
  target_compile_options(fonix_shim PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(fonix_shim PRIVATE -fsanitize=address,undefined)
elseif(FONIX_TEST_THREAD_SANITIZER AND NOT MSVC)
  target_compile_options(fonix_shim PRIVATE -fsanitize=thread -fno-omit-frame-pointer)
  target_link_options(fonix_shim PRIVATE -fsanitize=thread)
endif()

function(add_fake_ort target behavior output_directory)
  add_library(${target} SHARED fake_ort.c)
  target_compile_definitions(${target} PRIVATE FONIX_FAKE_ORT_BEHAVIOR=${behavior})
  target_include_directories(${target} SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}")
  set_target_properties(
    ${target} PROPERTIES
    C_VISIBILITY_PRESET hidden
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/${output_directory}"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/${output_directory}"
  )
  fonix_strict_target(${target})
endfunction()

add_fake_ort(fake_ort_good 1 "fake/allowed")
add_fake_ort(fake_ort_unsupported 2 "fake/unsupported")
add_fake_ort(fake_ort_bad_discovery 3 "fake/bad_discovery")
add_fake_ort(fake_ort_partial_discovery 4 "fake/partial_discovery")

if(APPLE)
  set(_fonix_exact_process_target "macos")
  set(_fonix_exact_process_basename "libonnxruntime.1.27.0.dylib")
  set(_fonix_wrong_process_basename "libonnxruntime.dylib")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(_fonix_exact_process_target "linux")
  set(_fonix_exact_process_basename "libonnxruntime.so")
  set(_fonix_wrong_process_basename "libonnxruntime.so.1")
endif()

if(DEFINED _fonix_exact_process_target)
  set(FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO "fonix-test-build-info-v1")
  string(
    SHA256
    _fonix_exact_process_build_info_sha256
    "${FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO}"
  )
  set(_fonix_exact_process_directory "${CMAKE_CURRENT_BINARY_DIR}/exact_process")
  set(_fonix_wrong_process_directory "${CMAKE_CURRENT_BINARY_DIR}/wrong_process")
  set(_fonix_search_process_directory "${CMAKE_CURRENT_BINARY_DIR}/search_process")
  set(_fonix_missing_process_directory "${CMAKE_CURRENT_BINARY_DIR}/missing_process")
  set(_fonix_symlink_process_directory "${CMAKE_CURRENT_BINARY_DIR}/symlink_process")
  set(_fonix_stale_process_directory "${CMAKE_CURRENT_BINARY_DIR}/stale_process")
  set(_fonix_null_process_directory "${CMAKE_CURRENT_BINARY_DIR}/null_process")
  set(_fonix_oversize_process_directory "${CMAKE_CURRENT_BINARY_DIR}/oversize_process")
  set(_fonix_prefix_process_directory "${CMAKE_CURRENT_BINARY_DIR}/prefix_process")
  set(_fonix_suffix_process_directory "${CMAKE_CURRENT_BINARY_DIR}/suffix_process")
  set(_fonix_nonascii_process_directory "${CMAKE_CURRENT_BINARY_DIR}/nonascii_process")
  set(_fonix_exact_process_include "${CMAKE_CURRENT_BINARY_DIR}/exact_process_private")
  file(MAKE_DIRECTORY "${_fonix_exact_process_include}")
  configure_file(
    "${CMAKE_CURRENT_LIST_DIR}/../../src/fonix_process_runtime_identity.h.in"
    "${_fonix_exact_process_include}/fonix_process_runtime_identity.h"
    @ONLY
  )

  add_library(fake_ort_exact_process SHARED fake_ort.c)
  target_compile_definitions(
    fake_ort_exact_process PRIVATE FONIX_FAKE_ORT_BEHAVIOR=1
  )
  target_include_directories(
    fake_ort_exact_process SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
  )
  set_target_properties(
    fake_ort_exact_process PROPERTIES
    C_VISIBILITY_PRESET hidden
    PREFIX ""
    SUFFIX ""
    OUTPUT_NAME "${_fonix_exact_process_basename}"
    LIBRARY_OUTPUT_DIRECTORY "${_fonix_exact_process_directory}"
  )
  fonix_strict_target(fake_ort_exact_process)

  add_library(fake_ort_wrong_process SHARED fake_ort.c)
  target_compile_definitions(
    fake_ort_wrong_process PRIVATE FONIX_FAKE_ORT_BEHAVIOR=1
  )
  target_include_directories(
    fake_ort_wrong_process SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
  )
  set_target_properties(
    fake_ort_wrong_process PROPERTIES
    C_VISIBILITY_PRESET hidden
    PREFIX ""
    SUFFIX ""
    OUTPUT_NAME "${_fonix_wrong_process_basename}"
    LIBRARY_OUTPUT_DIRECTORY "${_fonix_wrong_process_directory}"
  )
  fonix_strict_target(fake_ort_wrong_process)

  add_library(fake_ort_search_process SHARED fake_ort.c)
  target_compile_definitions(
    fake_ort_search_process PRIVATE FONIX_FAKE_ORT_BEHAVIOR=1
  )
  target_include_directories(
    fake_ort_search_process SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
  )
  set_target_properties(
    fake_ort_search_process PROPERTIES
    C_VISIBILITY_PRESET hidden
    PREFIX ""
    SUFFIX ""
    OUTPUT_NAME "${_fonix_exact_process_basename}"
    LIBRARY_OUTPUT_DIRECTORY "${_fonix_search_process_directory}"
  )
  fonix_strict_target(fake_ort_search_process)

  function(add_process_identity_fake target output_directory build_info_mode)
    add_library(${target} SHARED fake_ort.c)
    target_compile_definitions(
      ${target} PRIVATE
      FONIX_FAKE_ORT_BEHAVIOR=1
      FONIX_FAKE_ORT_BUILD_INFO_MODE=${build_info_mode}
    )
    target_include_directories(
      ${target} SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
    )
    set_target_properties(
      ${target} PROPERTIES
      C_VISIBILITY_PRESET hidden
      PREFIX ""
      SUFFIX ""
      OUTPUT_NAME "${_fonix_exact_process_basename}"
      LIBRARY_OUTPUT_DIRECTORY "${output_directory}"
    )
    fonix_strict_target(${target})
  endfunction()

  add_process_identity_fake(
    fake_ort_stale_process "${_fonix_stale_process_directory}" 1
  )
  add_process_identity_fake(
    fake_ort_null_process "${_fonix_null_process_directory}" 2
  )
  add_process_identity_fake(
    fake_ort_oversize_process "${_fonix_oversize_process_directory}" 3
  )
  add_process_identity_fake(
    fake_ort_prefix_process "${_fonix_prefix_process_directory}" 4
  )
  add_process_identity_fake(
    fake_ort_suffix_process "${_fonix_suffix_process_directory}" 5
  )
  add_process_identity_fake(
    fake_ort_nonascii_process "${_fonix_nonascii_process_directory}" 6
  )

  set(_fonix_exact_process_sources
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_core.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_loader_posix.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_loader_windows.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_memory.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_provider.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_runtime.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_run.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_session.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_status.c"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_tensor.c"
  )
  add_library(fonix_exact_process_shim SHARED ${_fonix_exact_process_sources})
  string(TOUPPER "${_fonix_exact_process_target}" _fonix_exact_target_upper)
  set(_fonix_exact_process_build_id
      "process-${_fonix_exact_process_target}-${_fonix_exact_process_basename}-buildinfo-${_fonix_exact_process_build_info_sha256}")
  target_compile_definitions(
    fonix_exact_process_shim PRIVATE
    FONIX_SHIM_BUILDING=1
    FONIX_RUNTIME_PROFILE_EXTERNAL=1
    FONIX_RUNTIME_PROFILE="external"
    FONIX_SHIM_BUILD_ID="${_fonix_exact_process_build_id}"
    FONIX_PROCESS_RUNTIME_BASENAME="${_fonix_exact_process_basename}"
    FONIX_PROCESS_RUNTIME_TARGET_${_fonix_exact_target_upper}=1
    FONIX_PROCESS_RUNTIME_IDENTITY_HEADER=1
  )
  target_include_directories(
    fonix_exact_process_shim PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src"
    "${ONNXRUNTIME_INCLUDE_DIR}"
    "${_fonix_exact_process_include}"
  )
  target_link_libraries(
    fonix_exact_process_shim PRIVATE Threads::Threads ${CMAKE_DL_LIBS}
  )
  set_target_properties(
    fonix_exact_process_shim PROPERTIES
    OUTPUT_NAME "fonix_shim"
    C_VISIBILITY_PRESET hidden
    POSITION_INDEPENDENT_CODE YES
    LIBRARY_OUTPUT_DIRECTORY "${_fonix_exact_process_directory}"
  )
  if(APPLE)
    target_link_options(
      fonix_exact_process_shim PRIVATE
      "-Wl,-exported_symbols_list,${CMAKE_CURRENT_LIST_DIR}/../../src/fonix_exports.apple"
    )
  elseif(UNIX)
    target_link_options(
      fonix_exact_process_shim PRIVATE
      "-Xlinker"
      "--version-script=${CMAKE_CURRENT_LIST_DIR}/../../src/fonix_exports.map"
      "-Wl,--no-undefined"
    )
  endif()
  fonix_strict_target(fonix_exact_process_shim)
  add_custom_command(
    TARGET fonix_exact_process_shim POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_missing_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_symlink_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_stale_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_null_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_oversize_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_prefix_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_suffix_process_directory}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${_fonix_nonascii_process_directory}"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_missing_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_symlink_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_stale_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_null_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_oversize_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_prefix_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_suffix_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_exact_process_shim>"
      "${_fonix_nonascii_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
    COMMAND
      "${CMAKE_COMMAND}" -E create_symlink
      "../exact_process/${_fonix_exact_process_basename}"
      "${_fonix_symlink_process_directory}/${_fonix_exact_process_basename}"
  )

  add_executable(fonix_exact_process_loader_test exact_process_loader_test.c)
  target_compile_definitions(
    fonix_exact_process_loader_test PRIVATE
    FONIX_TEST_EXACT_PROCESS_RUNTIME_BASENAME="${_fonix_exact_process_basename}"
    FONIX_TEST_WRONG_PROCESS_RUNTIME_BASENAME="${_fonix_wrong_process_basename}"
    FONIX_TEST_EXACT_PROCESS_BUILD_ID="${_fonix_exact_process_build_id}"
  )
  target_include_directories(
    fonix_exact_process_loader_test PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
  )
  target_link_libraries(
    fonix_exact_process_loader_test PRIVATE ${CMAKE_DL_LIBS}
  )
  add_dependencies(
    fonix_exact_process_loader_test
    fake_ort_exact_process
    fake_ort_wrong_process
    fake_ort_search_process
    fake_ort_stale_process
    fake_ort_null_process
    fake_ort_oversize_process
    fake_ort_prefix_process
    fake_ort_suffix_process
    fake_ort_nonascii_process
    fonix_exact_process_shim
  )
  fonix_strict_target(fonix_exact_process_loader_test)

  foreach(_fonix_mode IN ITEMS shim-first runtime-first exact-name wrong-first wrong-name)
    add_test(
      NAME exact-process-${_fonix_mode}
      COMMAND fonix_exact_process_loader_test
        "$<TARGET_FILE:fonix_exact_process_shim>"
        "$<TARGET_FILE:fake_ort_exact_process>"
        "$<TARGET_FILE:fake_ort_wrong_process>"
        "${_fonix_mode}"
    )
  endforeach()
  add_test(
    NAME exact-process-rejects-search-only
    COMMAND fonix_exact_process_loader_test
      "${_fonix_missing_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
      "$<TARGET_FILE:fake_ort_search_process>"
      "$<TARGET_FILE:fake_ort_wrong_process>"
      "search-only"
  )
  add_test(
    NAME exact-process-rejects-symlink
    COMMAND fonix_exact_process_loader_test
      "${_fonix_symlink_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
      "$<TARGET_FILE:fake_ort_exact_process>"
      "$<TARGET_FILE:fake_ort_wrong_process>"
      "symlink-adjacent"
  )
  foreach(_fonix_identity_mode IN ITEMS stale null oversize prefix suffix nonascii)
    add_test(
      NAME exact-process-rejects-${_fonix_identity_mode}-build-info
      COMMAND fonix_exact_process_loader_test
        "${_fonix_${_fonix_identity_mode}_process_directory}/$<TARGET_FILE_NAME:fonix_exact_process_shim>"
        "$<TARGET_FILE:fake_ort_${_fonix_identity_mode}_process>"
        "$<TARGET_FILE:fake_ort_wrong_process>"
        "${_fonix_identity_mode}-build-info"
    )
  endforeach()
  if(APPLE)
    set_tests_properties(
      exact-process-rejects-search-only PROPERTIES
      ENVIRONMENT "DYLD_LIBRARY_PATH=${_fonix_search_process_directory}"
    )
  else()
    set_tests_properties(
      exact-process-rejects-search-only PROPERTIES
      ENVIRONMENT "LD_LIBRARY_PATH=${_fonix_search_process_directory}"
    )
  endif()
endif()

add_library(fake_ort_missing_symbol SHARED fake_missing_symbol.c)
set_target_properties(
  fake_ort_missing_symbol PROPERTIES
  C_VISIBILITY_PRESET hidden
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/missing"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/missing"
)
fonix_strict_target(fake_ort_missing_symbol)

add_library(fake_ort_null_metadata SHARED fake_null_metadata_ort.c)
target_include_directories(
  fake_ort_null_metadata SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
set_target_properties(
  fake_ort_null_metadata PROPERTIES
  C_VISIBILITY_PRESET hidden
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/null_metadata"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/null_metadata"
)
fonix_strict_target(fake_ort_null_metadata)

add_library(fake_ort_allocation SHARED fake_null_metadata_ort.c)
target_compile_definitions(
  fake_ort_allocation PRIVATE FONIX_FAKE_ALLOCATION_EXTENSIONS=1
)
target_include_directories(
  fake_ort_allocation SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
set_target_properties(
  fake_ort_allocation PROPERTIES
  C_VISIBILITY_PRESET hidden
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/allocation"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/allocation"
)
fonix_strict_target(fake_ort_allocation)

add_library(fake_ort_desktop_providers SHARED fake_provider_ort.c)
target_include_directories(
  fake_ort_desktop_providers SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
set_target_properties(
  fake_ort_desktop_providers PROPERTIES
  C_VISIBILITY_PRESET hidden
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/providers"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake/providers"
)
fonix_strict_target(fake_ort_desktop_providers)

add_executable(fonix_header_c header_c.c)
target_include_directories(fonix_header_c PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
fonix_strict_target(fonix_header_c)
add_test(NAME header-compiles-as-c COMMAND fonix_header_c)

add_executable(fonix_header_cpp header_cpp.cpp)
target_include_directories(fonix_header_cpp PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
fonix_strict_target(fonix_header_cpp)
add_test(NAME header-compiles-as-cpp COMMAND fonix_header_cpp)

add_executable(
  fonix_string_limit_tests
  string_limit_tests.c
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_status.c"
)
target_include_directories(
  fonix_string_limit_tests
  PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src"
    "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_string_limit_tests PRIVATE
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="native-tests"
)
fonix_strict_target(fonix_string_limit_tests)
add_test(NAME owned-string-and-status-limits COMMAND fonix_string_limit_tests)

add_executable(
  fonix_status_allocation_tests
  status_allocation_tests.c
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_status.c"
)
target_include_directories(
  fonix_status_allocation_tests
  PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src"
    "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_status_allocation_tests PRIVATE
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="native-tests"
  FONIX_ALLOCATION_TESTING=1
)
fonix_strict_target(fonix_status_allocation_tests)
add_test(
  NAME status-allocation-fallbacks
  COMMAND fonix_status_allocation_tests
)

add_executable(
  fonix_android_sherpa_owner_contract
  android_owner_contract.c
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_status.c"
)
target_include_directories(
  fonix_android_sherpa_owner_contract
  PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src"
    "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_android_sherpa_owner_contract PRIVATE
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="android-owner-sherpa-source-process"
  FONIX_ANDROID_RUNTIME_OWNER_SHERPA=1
  FONIX_ANDROID_RUNTIME_OWNER="sherpa"
  FONIX_ANDROID_ALLOWED_RUNTIME_SOURCE="process"
)
fonix_strict_target(fonix_android_sherpa_owner_contract)
add_test(
  NAME android-sherpa-owner-process-only
  COMMAND fonix_android_sherpa_owner_contract
)

add_executable(fonix_runtime_tests runtime_tests.c)
target_link_libraries(
  fonix_runtime_tests PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(fonix_runtime_tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
fonix_strict_target(fonix_runtime_tests)
add_test(
  NAME runtime-loader-and-abi
  COMMAND fonix_runtime_tests
    "$<TARGET_FILE:fake_ort_good>"
    "$<TARGET_FILE_DIR:fake_ort_good>"
    "$<TARGET_FILE:fake_ort_missing_symbol>"
    "$<TARGET_FILE:fake_ort_unsupported>"
    "$<TARGET_FILE:fake_ort_bad_discovery>"
    "$<TARGET_FILE_DIR:fake_ort_bad_discovery>"
    "$<TARGET_FILE:fake_ort_partial_discovery>"
    "$<TARGET_FILE_DIR:fake_ort_partial_discovery>"
)

add_executable(fonix_phase2_guard_tests phase2_guard_tests.c)
target_link_libraries(
  fonix_phase2_guard_tests PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_phase2_guard_tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_phase2_guard_tests)
add_test(
  NAME phase2-native-guards
  COMMAND fonix_phase2_guard_tests
    "$<TARGET_FILE:fake_ort_good>"
    "$<TARGET_FILE_DIR:fake_ort_good>"
)

add_executable(fonix_cancel_registry_tests cancel_registry_tests.c)
target_link_libraries(
  fonix_cancel_registry_tests PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_cancel_registry_tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_cancel_registry_tests)
add_test(
  NAME phase5-cancel-registry
  COMMAND fonix_cancel_registry_tests
    "$<TARGET_FILE:fake_ort_good>"
    "$<TARGET_FILE_DIR:fake_ort_good>"
)

add_executable(
  fonix_cancel_during_run_tests
  cancel_during_run_tests.c
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_run.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/dort_status.c"
)
target_include_directories(
  fonix_cancel_during_run_tests
  PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src"
    "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_cancel_during_run_tests PRIVATE
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="native-tests"
  FONIX_PROFILE_TESTING=1
  DORT_CANCEL_TOKEN_UPPER_BOUND=32u
)
target_link_libraries(fonix_cancel_during_run_tests PRIVATE Threads::Threads)
fonix_strict_target(fonix_cancel_during_run_tests)
add_test(
  NAME phase5-cancel-during-run
  COMMAND fonix_cancel_during_run_tests
)

add_executable(fonix_phase3_null_metadata_guard phase3_null_metadata_guard.c)
target_link_libraries(
  fonix_phase3_null_metadata_guard
  PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_phase3_null_metadata_guard
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_phase3_null_metadata_guard)
add_test(
  NAME phase3-null-metadata-guard
  COMMAND fonix_phase3_null_metadata_guard
    "$<TARGET_FILE:fake_ort_null_metadata>"
    "$<TARGET_FILE_DIR:fake_ort_null_metadata>"
)

add_executable(fonix_allocation_fault_tests allocation_fault_tests.c)
target_link_libraries(
  fonix_allocation_fault_tests
  PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_allocation_fault_tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_allocation_fault_tests)
add_test(
  NAME shim-allocation-fault-injection
  COMMAND fonix_allocation_fault_tests
    "$<TARGET_FILE:fake_ort_good>"
    "$<TARGET_FILE_DIR:fake_ort_good>"
    "$<TARGET_FILE:fake_ort_allocation>"
    "$<TARGET_FILE_DIR:fake_ort_allocation>"
)

add_executable(fonix_shared_value_thread_tests shared_value_thread_tests.c)
target_link_libraries(
  fonix_shared_value_thread_tests
  PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_shared_value_thread_tests
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_shared_value_thread_tests)
add_test(
  NAME phase2-shared-value-thread-safety
  COMMAND fonix_shared_value_thread_tests
    "$<TARGET_FILE:fake_ort_null_metadata>"
    "$<TARGET_FILE_DIR:fake_ort_null_metadata>"
)

add_executable(fonix_desktop_provider_adapter_tests provider_adapter_tests.c)
target_link_libraries(
  fonix_desktop_provider_adapter_tests
  PRIVATE
    fonix_shim
    fake_ort_desktop_providers
    Threads::Threads
    ${CMAKE_DL_LIBS}
)
target_include_directories(
  fonix_desktop_provider_adapter_tests
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
)
fonix_strict_target(fonix_desktop_provider_adapter_tests)
add_test(
  NAME desktop-provider-adapters
  COMMAND fonix_desktop_provider_adapter_tests
    "$<TARGET_FILE:fake_ort_desktop_providers>"
    "$<TARGET_FILE_DIR:fake_ort_desktop_providers>"
)

set(FONIX_REAL_ORT_LIBRARY "" CACHE FILEPATH "Optional API-27 ORT library for numeric tests")
set(FONIX_REAL_ORT_ROOT "" CACHE PATH "Allowed root containing FONIX_REAL_ORT_LIBRARY")
set(FONIX_REAL_MODEL "" CACHE FILEPATH "Optional ONNX model for numeric tests")
set(FONIX_REAL_MODEL_ROOT "" CACHE PATH "Allowed root containing FONIX_REAL_MODEL")
if(FONIX_REAL_ORT_LIBRARY AND FONIX_REAL_ORT_ROOT AND FONIX_REAL_MODEL AND
   FONIX_REAL_MODEL_ROOT)
  foreach(_fonix_real_path IN ITEMS
      "${FONIX_REAL_ORT_LIBRARY}"
      "${FONIX_REAL_ORT_ROOT}"
      "${FONIX_REAL_MODEL}"
      "${FONIX_REAL_MODEL_ROOT}")
    if(NOT EXISTS "${_fonix_real_path}")
      message(FATAL_ERROR "Configured real Phase 2 test path is missing: ${_fonix_real_path}")
    endif()
  endforeach()
  add_executable(fonix_real_ort_tests real_ort_tests.c)
  target_link_libraries(
    fonix_real_ort_tests PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
  )
  target_include_directories(
    fonix_real_ort_tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
  )
  fonix_strict_target(fonix_real_ort_tests)
  add_test(
    NAME phase2-real-ort-cpu
    COMMAND fonix_real_ort_tests
      "${FONIX_REAL_ORT_LIBRARY}"
      "${FONIX_REAL_ORT_ROOT}"
      "${FONIX_REAL_MODEL}"
      "${FONIX_REAL_MODEL_ROOT}"
  )

  add_executable(fonix_phase3_real_ort_tests phase3_real_ort_tests.c)
  target_link_libraries(
    fonix_phase3_real_ort_tests
    PRIVATE fonix_shim Threads::Threads ${CMAKE_DL_LIBS}
  )
  target_include_directories(
    fonix_phase3_real_ort_tests
    PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src"
  )
  fonix_strict_target(fonix_phase3_real_ort_tests)
  add_test(
    NAME phase3-real-ort-cpu
    COMMAND fonix_phase3_real_ort_tests
      "${FONIX_REAL_ORT_LIBRARY}"
      "${FONIX_REAL_ORT_ROOT}"
      "${CMAKE_CURRENT_LIST_DIR}/../fixtures"
  )
  set_tests_properties(
    phase3-real-ort-cpu PROPERTIES
    ENVIRONMENT
      "FONIX_TEST_ALLOCATION_EPOCH=1;FONIX_TEST_ALLOCATION_FAIL_AT=1"
  )
endif()

find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(
  NAME exported-symbol-allowlist
  COMMAND
    "${Python3_EXECUTABLE}"
    -I
    "${CMAKE_CURRENT_LIST_DIR}/check_exports.py"
    "$<TARGET_FILE:fonix_shim>"
)
add_test(
  NAME external-shim-has-no-ort-link
  COMMAND
    "${Python3_EXECUTABLE}"
    -I
    "${CMAKE_CURRENT_LIST_DIR}/check_no_ort_dependency.py"
    "$<TARGET_FILE:fonix_shim>"
)
