cmake_minimum_required(VERSION 3.18)
project(fonix_phase3_fixtures C)

enable_testing()

find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_test(
  NAME phase3_fixture_bytes
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/generate_phase3_fixtures.py" --check)

set(FONIX_REAL_ORT_LIBRARY "" CACHE FILEPATH
    "Optional ONNX Runtime shared library used to validate the fixture corpus")
set(FONIX_EXPECTED_ORT_VERSION "" CACHE STRING
    "Optional exact ONNX Runtime version asserted by the fixture validator")

if(FONIX_REAL_ORT_LIBRARY)
  if(NOT UNIX)
    message(FATAL_ERROR
      "The standalone Phase-3 ORT validator currently supports POSIX loaders only")
  endif()
  if(NOT EXISTS "${FONIX_REAL_ORT_LIBRARY}")
    message(FATAL_ERROR
      "FONIX_REAL_ORT_LIBRARY does not exist: ${FONIX_REAL_ORT_LIBRARY}")
  endif()

  add_executable(fonix_phase3_fixture_validator validate_phase3_with_ort.c)
  target_compile_features(fonix_phase3_fixture_validator PRIVATE c_std_11)
  target_include_directories(
    fonix_phase3_fixture_validator
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/onnxruntime/include")
  target_link_libraries(fonix_phase3_fixture_validator PRIVATE ${CMAKE_DL_LIBS})
  if(CMAKE_C_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
    target_compile_options(
      fonix_phase3_fixture_validator
      PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
        -Wconversion
        -Wshadow
        -Wstrict-prototypes
        -Wmissing-prototypes)
  endif()

  set(_validator_arguments
      "${FONIX_REAL_ORT_LIBRARY}" "${CMAKE_CURRENT_SOURCE_DIR}")
  if(FONIX_EXPECTED_ORT_VERSION)
    list(APPEND _validator_arguments "${FONIX_EXPECTED_ORT_VERSION}")
  endif()
  add_test(
    NAME phase3_fixture_real_ort
    COMMAND fonix_phase3_fixture_validator ${_validator_arguments})
endif()
