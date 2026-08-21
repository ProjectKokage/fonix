cmake_minimum_required(VERSION 3.24)
project(fonix_shim VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

set(FONIX_RUNTIME_PROFILE "external" CACHE STRING "external, linked, or bundled")
set_property(CACHE FONIX_RUNTIME_PROFILE PROPERTY STRINGS external linked bundled)
if(NOT FONIX_RUNTIME_PROFILE STREQUAL "external" AND
   NOT FONIX_RUNTIME_PROFILE STREQUAL "linked" AND
   NOT FONIX_RUNTIME_PROFILE STREQUAL "bundled")
  message(FATAL_ERROR "FONIX_RUNTIME_PROFILE must be external, linked, or bundled")
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "iOS" AND FONIX_RUNTIME_PROFILE STREQUAL "external")
  message(FATAL_ERROR "iOS does not support the external PROCESS/FILE loader profile")
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
  set(_fonix_default_library_type STATIC)
else()
  set(_fonix_default_library_type SHARED)
endif()
set(FONIX_SHIM_LIBRARY_TYPE "${_fonix_default_library_type}" CACHE STRING "STATIC or SHARED")
set_property(CACHE FONIX_SHIM_LIBRARY_TYPE PROPERTY STRINGS STATIC SHARED)
if(NOT FONIX_SHIM_LIBRARY_TYPE STREQUAL "STATIC" AND
   NOT FONIX_SHIM_LIBRARY_TYPE STREQUAL "SHARED")
  message(FATAL_ERROR "FONIX_SHIM_LIBRARY_TYPE must be STATIC or SHARED")
endif()

set(FONIX_SHIM_BUILD_ID "development" CACHE STRING "Diagnostic build identifier")
if(NOT FONIX_SHIM_BUILD_ID MATCHES "^[A-Za-z0-9._-]+$")
  message(FATAL_ERROR "FONIX_SHIM_BUILD_ID must contain only ASCII letters, digits, dot, underscore, or hyphen")
endif()

set(
  FONIX_PROCESS_RUNTIME_BASENAME
  ""
  CACHE STRING
  "Optional exact app-owned process runtime basename for this desktop target"
)
set(
  FONIX_PROCESS_RUNTIME_TARGET
  ""
  CACHE STRING
  "Target key paired with FONIX_PROCESS_RUNTIME_BASENAME: macos, linux, or windows"
)
set(
  FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO
  ""
  CACHE STRING
  "Optional exact OrtApi GetBuildInfoString value for the process runtime"
)
if((FONIX_PROCESS_RUNTIME_BASENAME AND NOT FONIX_PROCESS_RUNTIME_TARGET) OR
   (FONIX_PROCESS_RUNTIME_TARGET AND NOT FONIX_PROCESS_RUNTIME_BASENAME))
  message(FATAL_ERROR "FONIX_PROCESS_RUNTIME_BASENAME and FONIX_PROCESS_RUNTIME_TARGET must be set together")
endif()
if(FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO AND
   NOT FONIX_PROCESS_RUNTIME_BASENAME)
  message(FATAL_ERROR "FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO requires an exact process runtime basename")
endif()

set(_fonix_effective_build_id "${FONIX_SHIM_BUILD_ID}")
set(_fonix_process_runtime_definitions)
set(_fonix_process_runtime_include)
if(FONIX_PROCESS_RUNTIME_BASENAME)
  if(NOT FONIX_RUNTIME_PROFILE STREQUAL "external")
    message(FATAL_ERROR "An exact process runtime basename requires the external runtime profile")
  endif()
  if(NOT FONIX_PROCESS_RUNTIME_BASENAME MATCHES "^[A-Za-z0-9][A-Za-z0-9._-]*$")
    message(FATAL_ERROR "FONIX_PROCESS_RUNTIME_BASENAME must be one safe library basename")
  endif()
  if(FONIX_PROCESS_RUNTIME_TARGET STREQUAL "macos")
    if(NOT APPLE OR CMAKE_SYSTEM_NAME STREQUAL "iOS" OR
       NOT FONIX_PROCESS_RUNTIME_BASENAME MATCHES "^lib[A-Za-z0-9][A-Za-z0-9._-]*\\.dylib$")
      message(FATAL_ERROR "The macos process runtime contract requires a macOS lib*.dylib target")
    endif()
  elseif(FONIX_PROCESS_RUNTIME_TARGET STREQUAL "linux")
    if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR
       NOT FONIX_PROCESS_RUNTIME_BASENAME MATCHES "^lib[A-Za-z0-9][A-Za-z0-9._-]*\\.so(\\.[0-9]+)*$")
      message(FATAL_ERROR "The linux process runtime contract requires a Linux lib*.so target")
    endif()
  elseif(FONIX_PROCESS_RUNTIME_TARGET STREQUAL "windows")
    if(NOT WIN32 OR
       NOT FONIX_PROCESS_RUNTIME_BASENAME MATCHES "^[A-Za-z0-9][A-Za-z0-9._-]*\\.dll$")
      message(FATAL_ERROR "The windows process runtime contract requires a Windows *.dll target")
    endif()
  else()
    message(FATAL_ERROR "FONIX_PROCESS_RUNTIME_TARGET must be macos, linux, or windows")
  endif()
  string(TOUPPER "${FONIX_PROCESS_RUNTIME_TARGET}" _fonix_process_runtime_target_upper)
  set(
    _fonix_effective_build_id
    "process-${FONIX_PROCESS_RUNTIME_TARGET}-${FONIX_PROCESS_RUNTIME_BASENAME}"
  )
  if(FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO)
    string(
      LENGTH
      "${FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO}"
      _fonix_process_runtime_build_info_length
    )
    if(_fonix_process_runtime_build_info_length GREATER 1024 OR
       NOT FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO MATCHES "^[A-Za-z0-9][A-Za-z0-9 ._:@,=+/\\-]*$")
      message(FATAL_ERROR "FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO must contain 1 to 1024 closed printable ASCII bytes")
    endif()
    string(
      SHA256
      _fonix_process_runtime_build_info_sha256
      "${FONIX_PROCESS_RUNTIME_EXPECTED_BUILD_INFO}"
    )
    string(
      APPEND
      _fonix_effective_build_id
      "-buildinfo-${_fonix_process_runtime_build_info_sha256}"
    )
    set(_fonix_process_runtime_include "${CMAKE_CURRENT_BINARY_DIR}/fonix_private")
    file(MAKE_DIRECTORY "${_fonix_process_runtime_include}")
    configure_file(
      "${CMAKE_CURRENT_LIST_DIR}/fonix_process_runtime_identity.h.in"
      "${_fonix_process_runtime_include}/fonix_process_runtime_identity.h"
      @ONLY
    )
    list(
      APPEND _fonix_process_runtime_definitions
      FONIX_PROCESS_RUNTIME_IDENTITY_HEADER=1
    )
  endif()
  string(LENGTH "${_fonix_effective_build_id}" _fonix_effective_build_id_length)
  if(_fonix_effective_build_id_length GREATER 128 OR
     NOT _fonix_effective_build_id MATCHES "^[A-Za-z0-9._-]+$")
    message(FATAL_ERROR "The exact process runtime contract exceeds the build ID limit")
  endif()
  list(
    APPEND _fonix_process_runtime_definitions
    FONIX_PROCESS_RUNTIME_BASENAME="${FONIX_PROCESS_RUNTIME_BASENAME}"
    FONIX_PROCESS_RUNTIME_TARGET_${_fonix_process_runtime_target_upper}=1
  )
endif()

set(
  ONNXRUNTIME_INCLUDE_DIR
  "${CMAKE_CURRENT_LIST_DIR}/../third_party/onnxruntime/include"
  CACHE PATH
  "Exact pinned ONNX Runtime API-27 include directory"
)
set(ONNXRUNTIME_LIBRARY "" CACHE FILEPATH "Exact pinned linked ONNX Runtime library")

set(_ort_header "${ONNXRUNTIME_INCLUDE_DIR}/onnxruntime_c_api.h")
if(NOT EXISTS "${_ort_header}")
  set(_ort_header "${ONNXRUNTIME_INCLUDE_DIR}/onnxruntime/core/session/onnxruntime_c_api.h")
endif()
if(NOT EXISTS "${_ort_header}")
  message(FATAL_ERROR "A verified API-27 onnxruntime_c_api.h is required")
endif()
file(
  STRINGS "${_ort_header}" _ort_api_definitions
  REGEX "^[ \t]*#[ \t]*define[ \t]+ORT_API_VERSION[ \t]+[0-9]+"
)
if(NOT _ort_api_definitions)
  message(FATAL_ERROR "Could not determine ORT_API_VERSION from ${_ort_header}")
endif()
list(GET _ort_api_definitions 0 _ort_api_definition)
string(REGEX MATCH "[0-9]+" _ort_header_api "${_ort_api_definition}")
if(NOT _ort_header_api EQUAL 27)
  message(FATAL_ERROR "Fonix shim ABI 1 must compile against exact ORT C API 27 headers; found API ${_ort_header_api}")
endif()

add_library(
  fonix_shim ${FONIX_SHIM_LIBRARY_TYPE}
  dort_core.c
  dort_loader_posix.c
  dort_loader_windows.c
  dort_memory.c
  dort_provider.c
  dort_runtime.c
  dort_run.c
  dort_session.c
  dort_status.c
  dort_tensor.c
)

set_target_properties(
  fonix_shim PROPERTIES
  OUTPUT_NAME "fonix_shim"
  C_VISIBILITY_PRESET hidden
  POSITION_INDEPENDENT_CODE YES
)

string(TOUPPER "${FONIX_RUNTIME_PROFILE}" _fonix_runtime_profile_upper)
target_compile_definitions(
  fonix_shim PRIVATE
  FONIX_SHIM_BUILDING=1
  FONIX_RUNTIME_PROFILE_${_fonix_runtime_profile_upper}=1
  FONIX_RUNTIME_PROFILE="${FONIX_RUNTIME_PROFILE}"
  FONIX_SHIM_BUILD_ID="${_fonix_effective_build_id}"
  ${_fonix_process_runtime_definitions}
)
if(FONIX_SHIM_LIBRARY_TYPE STREQUAL "STATIC")
  target_compile_definitions(fonix_shim PUBLIC FONIX_SHIM_STATIC=1)
endif()

target_include_directories(
  fonix_shim
  PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_LIST_DIR}>"
    "$<INSTALL_INTERFACE:include>"
)
target_include_directories(fonix_shim SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}")
if(_fonix_process_runtime_include)
  target_include_directories(
    fonix_shim PRIVATE "${_fonix_process_runtime_include}"
  )
endif()

find_package(Threads REQUIRED)
target_link_libraries(fonix_shim PRIVATE Threads::Threads ${CMAKE_DL_LIBS})
if(WIN32)
  target_link_libraries(fonix_shim PRIVATE bcrypt advapi32)
endif()

if(FONIX_RUNTIME_PROFILE STREQUAL "linked")
  if(NOT ONNXRUNTIME_LIBRARY OR NOT EXISTS "${ONNXRUNTIME_LIBRARY}")
    message(FATAL_ERROR "The linked profile requires an exact ONNXRUNTIME_LIBRARY path")
  endif()
  add_library(fonix_onnxruntime_pinned UNKNOWN IMPORTED GLOBAL)
  set_target_properties(
    fonix_onnxruntime_pinned PROPERTIES IMPORTED_LOCATION "${ONNXRUNTIME_LIBRARY}"
  )
  target_link_libraries(fonix_shim PRIVATE fonix_onnxruntime_pinned)
endif()

if(MSVC)
  target_compile_options(fonix_shim PRIVATE /W4 /WX)
else()
  target_compile_options(
    fonix_shim PRIVATE
    -Wall
    -Wextra
    -Wpedantic
    -Wconversion
    -Wshadow
    -Werror
    -fvisibility=hidden
    -fstack-protector-strong
  )
endif()

if(FONIX_SHIM_LIBRARY_TYPE STREQUAL "SHARED")
  if(WIN32)
    target_sources(fonix_shim PRIVATE fonix_shim.def)
  elseif(APPLE)
    set_property(
      TARGET fonix_shim APPEND PROPERTY
      LINK_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/fonix_exports.apple"
    )
    target_link_options(
      fonix_shim PRIVATE
      "-Wl,-exported_symbols_list,${CMAKE_CURRENT_LIST_DIR}/fonix_exports.apple"
    )
  elseif(UNIX)
    set_property(
      TARGET fonix_shim APPEND PROPERTY
      LINK_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/fonix_exports.map"
    )
    target_link_options(
      fonix_shim PRIVATE
      "$<$<PLATFORM_ID:Linux>:-fuse-ld=lld>"
      "-Xlinker"
      "--version-script=${CMAKE_CURRENT_LIST_DIR}/fonix_exports.map"
      "-Wl,--no-undefined"
      "-Wl,-z,relro"
      "-Wl,-z,now"
      "-Wl,-z,noexecstack"
    )
  endif()
endif()

install(
  TARGETS fonix_shim
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
  ARCHIVE DESTINATION lib
)
install(FILES dort.h DESTINATION include)
