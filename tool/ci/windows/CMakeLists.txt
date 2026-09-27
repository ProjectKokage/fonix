cmake_minimum_required(VERSION 3.24)
project(fonix_windows_contract_tests LANGUAGES C)

if(NOT WIN32)
  message(FATAL_ERROR "The Windows contract suite requires a Windows host")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

include(CTest)
enable_testing()

set(FONIX_RUNTIME_PROFILE external CACHE STRING "" FORCE)
set(FONIX_SHIM_LIBRARY_TYPE SHARED CACHE STRING "" FORCE)
set(FONIX_SHIM_BUILD_ID windows-contract-tests CACHE STRING "" FORCE)
set(
  ONNXRUNTIME_INCLUDE_DIR
  "${CMAKE_CURRENT_LIST_DIR}/../../../third_party/onnxruntime/include"
  CACHE PATH "" FORCE
)
add_subdirectory(
  "${CMAKE_CURRENT_LIST_DIR}/../../../src"
  fonix_shim_build
)

add_executable(fonix_windows_contract_test windows_contract_test.c)
target_include_directories(
  fonix_windows_contract_test
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../src"
)
target_link_libraries(fonix_windows_contract_test PRIVATE fonix_shim)
if(MSVC)
  target_compile_options(fonix_windows_contract_test PRIVATE /W4 /WX /utf-8 /experimental:c11atomics)
else()
  message(FATAL_ERROR "The Windows CI contract currently requires MSVC")
endif()
add_custom_command(
  TARGET fonix_windows_contract_test
  POST_BUILD
  COMMAND
    "${CMAKE_COMMAND}" -E copy_if_different
    "$<TARGET_FILE:fonix_shim>"
    "$<TARGET_FILE_DIR:fonix_windows_contract_test>"
)
add_test(NAME windows-shim-contract COMMAND fonix_windows_contract_test)

add_executable(
  fonix_windows_path_security_test
  windows_path_security_test.c
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_loader_posix.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_loader_windows.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_run.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_runtime.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_session.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_status.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_tensor.c"
)
target_include_directories(
  fonix_windows_path_security_test
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../src"
)
target_include_directories(
  fonix_windows_path_security_test
  SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_windows_path_security_test PRIVATE
  FONIX_SHIM_STATIC=1
  FONIX_WINDOWS_PATH_TESTING=1
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="windows-path-security-tests"
)
target_link_libraries(fonix_windows_path_security_test PRIVATE bcrypt advapi32)
if(MSVC)
  target_compile_options(
    fonix_windows_path_security_test PRIVATE /W4 /WX /utf-8 /experimental:c11atomics
  )
else()
  message(FATAL_ERROR "The Windows path security test currently requires MSVC")
endif()
add_test(NAME windows-path-security COMMAND fonix_windows_path_security_test)

add_executable(
  fonix_windows_profile_security_test
  windows_profile_security_test.c
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_core.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_memory.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_loader_posix.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_loader_windows.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_run.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_runtime.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_session.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_status.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/dort_tensor.c"
)
target_include_directories(
  fonix_windows_profile_security_test
  PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../src"
)
target_include_directories(
  fonix_windows_profile_security_test
  SYSTEM PRIVATE "${ONNXRUNTIME_INCLUDE_DIR}"
)
target_compile_definitions(
  fonix_windows_profile_security_test PRIVATE
  FONIX_SHIM_STATIC=1
  FONIX_WINDOWS_PROFILE_TESTING=1
  FONIX_RUNTIME_PROFILE_EXTERNAL=1
  FONIX_RUNTIME_PROFILE="external"
  FONIX_SHIM_BUILD_ID="windows-profile-security-tests"
)
target_link_libraries(
  fonix_windows_profile_security_test PRIVATE bcrypt advapi32
)
if(MSVC)
  target_compile_options(
    fonix_windows_profile_security_test PRIVATE /W4 /WX /utf-8 /experimental:c11atomics
  )
else()
  message(FATAL_ERROR "The Windows profile security test currently requires MSVC")
endif()
add_test(
  NAME windows-profile-security
  COMMAND fonix_windows_profile_security_test
)

find_package(Python3 REQUIRED COMPONENTS Interpreter)
get_filename_component(_fonix_linker_directory "${CMAKE_LINKER}" DIRECTORY)
get_filename_component(_fonix_compiler_directory "${CMAKE_C_COMPILER}" DIRECTORY)
find_program(
  FONIX_DUMPBIN_EXECUTABLE
  NAMES dumpbin dumpbin.exe
  HINTS "${_fonix_linker_directory}" "${_fonix_compiler_directory}"
  REQUIRED
)
add_test(
  NAME windows-binary-contract
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_LIST_DIR}/../check_windows_binary.py"
    --dumpbin "${FONIX_DUMPBIN_EXECUTABLE}"
    --library "$<TARGET_FILE:fonix_shim>"
    --exports "${CMAKE_CURRENT_LIST_DIR}/../../../src/fonix_shim.def"
)
