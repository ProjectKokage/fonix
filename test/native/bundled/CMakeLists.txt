cmake_minimum_required(VERSION 3.24)
project(fonix_bundled_runtime_tests LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

include(CTest)
enable_testing()

set(FONIX_RUNTIME_PROFILE bundled CACHE STRING "" FORCE)
set(FONIX_SHIM_LIBRARY_TYPE SHARED CACHE STRING "" FORCE)
set(FONIX_SHIM_BUILD_ID bundled-runtime-tests CACHE STRING "" FORCE)
set(
  ONNXRUNTIME_INCLUDE_DIR
  "${CMAKE_CURRENT_LIST_DIR}/../../../third_party/onnxruntime/include"
  CACHE PATH "" FORCE
)
add_subdirectory(
  "${CMAKE_CURRENT_LIST_DIR}/../../../src"
  fonix_shim_build
)

set(_fonix_bundle_directory "${CMAKE_CURRENT_BINARY_DIR}/bundle")
set_target_properties(
  fonix_shim PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
  RUNTIME_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
  ARCHIVE_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
)

add_library(fonix_bundled_fake_ort SHARED ../fake_ort.c)
target_compile_definitions(
  fonix_bundled_fake_ort PRIVATE FONIX_FAKE_ORT_BEHAVIOR=1
)
target_include_directories(
  fonix_bundled_fake_ort SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
set_target_properties(
  fonix_bundled_fake_ort PROPERTIES
  C_VISIBILITY_PRESET hidden
  LIBRARY_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
  RUNTIME_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
  ARCHIVE_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
)
if(APPLE)
  set_target_properties(
    fonix_bundled_fake_ort PROPERTIES OUTPUT_NAME "onnxruntime.1"
  )
elseif(WIN32)
  set_target_properties(
    fonix_bundled_fake_ort PROPERTIES OUTPUT_NAME "onnxruntime" PREFIX ""
  )
else()
  set_target_properties(
    fonix_bundled_fake_ort PROPERTIES OUTPUT_NAME "onnxruntime" SOVERSION 1
  )
endif()

add_executable(fonix_bundled_runtime_test bundled_runtime_test.c)
target_include_directories(
  fonix_bundled_runtime_test PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../src"
)
target_link_libraries(fonix_bundled_runtime_test PRIVATE fonix_shim)
set_target_properties(
  fonix_bundled_runtime_test PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${_fonix_bundle_directory}"
)
add_dependencies(fonix_bundled_runtime_test fonix_bundled_fake_ort)

if(MSVC)
  target_compile_options(fonix_bundled_fake_ort PRIVATE /W4 /WX /utf-8)
  target_compile_options(fonix_bundled_runtime_test PRIVATE /W4 /WX /utf-8)
else()
  foreach(_fonix_target IN ITEMS
      fonix_bundled_fake_ort
      fonix_bundled_runtime_test)
    target_compile_options(
      ${_fonix_target} PRIVATE
      -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror
    )
  endforeach()
endif()

add_test(
  NAME bundled-runtime-adjacent
  COMMAND "$<TARGET_FILE:fonix_bundled_runtime_test>"
)

if(NOT WIN32)
  function(fonix_add_android_bundled_loader_test target abi_definition)
    add_executable(
      ${target}
      android_bundled_loader_test.c
      "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_loader_posix.c"
      "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_core.c"
      "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_memory.c"
      "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_status.c"
    )
    target_include_directories(
      ${target}
      PRIVATE
      "${CMAKE_CURRENT_LIST_DIR}/../../../src"
      "${ONNXRUNTIME_INCLUDE_DIR}"
    )
    target_compile_definitions(
      ${target} PRIVATE
      FONIX_RUNTIME_PROFILE_BUNDLED=1
      FONIX_RUNTIME_PROFILE="bundled"
      FONIX_SHIM_BUILD_ID="android-bundled-loader-test"
      FONIX_TEST_ANDROID_BUNDLED_LOADER=1
      ${abi_definition}=1
      dladdr=fonix_test_dladdr
      dlopen=fonix_test_dlopen
      dlsym=fonix_test_dlsym
      dlerror=fonix_test_dlerror
      dlclose=fonix_test_dlclose
    )
    target_link_libraries(${target} PRIVATE ${CMAKE_DL_LIBS})
    target_compile_options(
      ${target} PRIVATE
      -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror
    )
  endfunction()

  fonix_add_android_bundled_loader_test(
    fonix_android_arm64_bundled_loader_test
    FONIX_TEST_ANDROID_ABI_ARM64
  )
  fonix_add_android_bundled_loader_test(
    fonix_android_x86_64_bundled_loader_test
    FONIX_TEST_ANDROID_ABI_X86_64
  )
  add_test(
    NAME bundled-runtime-android-arm64-apk-namespace
    COMMAND "$<TARGET_FILE:fonix_android_arm64_bundled_loader_test>"
  )
  add_test(
    NAME bundled-runtime-android-x86-64-apk-namespace
    COMMAND "$<TARGET_FILE:fonix_android_x86_64_bundled_loader_test>"
  )
endif()

if(APPLE)
  set(
    _fonix_flutter_frameworks
    "${CMAKE_CURRENT_BINARY_DIR}/flutter-app/Contents/Frameworks"
  )
  set(
    _fonix_flutter_shim
    "${_fonix_flutter_frameworks}/fonix_shim.framework/Versions/A/fonix_shim"
  )
  set(
    _fonix_flutter_runtime
    "${_fonix_flutter_frameworks}/onnxruntime.1.framework/Versions/A/onnxruntime.1"
  )
  set(
    _fonix_spoofed_shim
    "${CMAKE_CURRENT_BINARY_DIR}/unexpected-layout/fonix_shim"
  )
  add_executable(
    fonix_flutter_framework_runtime_test flutter_framework_runtime_test.c
  )
  target_include_directories(
    fonix_flutter_framework_runtime_test
    PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../src"
  )
  target_compile_options(
    fonix_flutter_framework_runtime_test PRIVATE
    -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror
  )
  add_dependencies(
    fonix_flutter_framework_runtime_test
    fonix_shim
    fonix_bundled_fake_ort
  )
  add_custom_command(
    TARGET fonix_flutter_framework_runtime_test POST_BUILD
    COMMAND
      "${CMAKE_COMMAND}" -E make_directory
      "${_fonix_flutter_frameworks}/fonix_shim.framework/Versions/A"
    COMMAND
      "${CMAKE_COMMAND}" -E make_directory
      "${_fonix_flutter_frameworks}/onnxruntime.1.framework/Versions/A"
    COMMAND
      "${CMAKE_COMMAND}" -E make_directory
      "${CMAKE_CURRENT_BINARY_DIR}/unexpected-layout"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_shim>" "${_fonix_flutter_shim}"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_bundled_fake_ort>" "${_fonix_flutter_runtime}"
    COMMAND
      "${CMAKE_COMMAND}" -E copy
      "$<TARGET_FILE:fonix_shim>" "${_fonix_spoofed_shim}"
    VERBATIM
  )
  add_test(
    NAME bundled-runtime-flutter-framework
    COMMAND
      "$<TARGET_FILE:fonix_flutter_framework_runtime_test>"
      "${_fonix_flutter_shim}" success
  )
  add_test(
    NAME bundled-runtime-rejects-unexpected-layout
    COMMAND
      "$<TARGET_FILE:fonix_flutter_framework_runtime_test>"
      "${_fonix_spoofed_shim}" reject
  )
endif()
