import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/ffi/native_api.dart';
import 'package:fonix/src/runtime.dart'
    show openOrtRuntimeWithNativeApiForTesting;
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  final String? runtimePath = Platform.environment['FONIX_TEST_REAL_ORT_PATH'];
  if (runtimePath == null) {
    test(
      'official ONNX Runtime Phase-3 fixture is provisioned',
      () {},
      skip: 'Set FONIX_TEST_REAL_ORT_PATH to ONNX Runtime v1.27.1.',
    );
    return;
  }

  OrtRuntime openRuntime() => OrtRuntime.open(
    source: OrtRuntimeSource.file(
      absolutePath: p.normalize(runtimePath),
      allowedRoot: p.dirname(p.normalize(runtimePath)),
    ),
    logId: 'fonix-phase3-dart-test',
  );

  OrtRuntime openRuntimeWithApi(FonixNativeApi nativeApi, String logId) =>
      openOrtRuntimeWithNativeApiForTesting(
        nativeApi: nativeApi,
        source: OrtRuntimeSource.file(
          absolutePath: p.normalize(runtimePath),
          allowedRoot: p.dirname(p.normalize(runtimePath)),
        ),
        logId: logId,
      );

  File fixture(String name) =>
      File(p.normalize(p.absolute('test', 'fixtures', name)));

  OrtSession openSession(OrtRuntime runtime, String name) =>
      OrtSession.fromFile(
        runtime: runtime,
        modelPath: fixture(name).path,
        allowedRoot: fixture(name).parent.path,
      );

  group('official ONNX Runtime Phase-3 values', () {
    test('string tensors copy UTF-8 and retained output owns its lifetime', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'string_identity.onnx');
      final List<String> expected = <String>[
        '',
        'ASCII',
        'こんにちは🌿',
        List<String>.filled(4096, 'x').join(),
      ];
      final OrtStringTensor input = OrtStringTensor.fromStrings(
        runtime: runtime,
        values: expected,
        shape: const <int>[4],
      );
      OrtRunResult? result;
      OrtStringTensor? retained;
      try {
        expect(
          () => OrtStringTensor.fromStrings(
            runtime: runtime,
            values: const <String>['bad\u0000value'],
            shape: const <int>[1],
          ),
          throwsArgumentError,
        );
        result = session.run(inputs: <String, OrtValue>{'text': input});
        final OrtStringTensor borrowed = result.stringTensor('echo');
        expect(borrowed.copyStrings(), expected);
        retained = borrowed.retain();

        result.dispose();
        session.dispose();
        runtime.dispose();
        expect(retained.copyStrings(), expected);
        retained.dispose();
        retained.dispose();
      } finally {
        retained?.dispose();
        result?.dispose();
        input.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('rank-zero float64 identity preserves scalar shape and value', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(
        runtime,
        'scalar_float64_identity.onnx',
      );
      final OrtTensor input = OrtTensor.fromFloat64List(
        runtime: runtime,
        values: Float64List.fromList(<double>[-3.25]),
        shape: const <int>[],
      );
      OrtRunResult? result;
      try {
        expect(session.inputs.single.name, 'scalar.input');
        expect(session.outputs.single.name, 'scalar/output');
        expect(session.inputs.single.type.hasShape, isTrue);
        expect(session.inputs.single.type.dimensions, isEmpty);
        expect(
          session.inputs.single.type.tensorElementType,
          OrtTensorElementType.float64,
        );

        result = session.run(inputs: <String, OrtValue>{'scalar.input': input});
        final OrtTensor output = result.tensor('scalar/output');
        expect(output.shape.isScalar, isTrue);
        expect(output.copyFloat64Data(), <double>[-3.25]);
      } finally {
        result?.dispose();
        input.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('dynamic Add accepts two concrete shapes with exact named I/O', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'dynamic_add.onnx');
      try {
        expect(session.inputs.map((OrtValueInfo value) => value.name), <String>[
          'lhs.matrix',
          'rhs/matrix',
        ]);
        expect(session.outputs.single.name, 'sum.matrix');
        for (final OrtValueInfo value in <OrtValueInfo>[
          ...session.inputs,
          ...session.outputs,
        ]) {
          expect(value.type.tensorElementType, OrtTensorElementType.float32);
          expect(
            value.type.dimensions.map(
              (OrtDimension dimension) => dimension.symbol,
            ),
            <String?>['rows', 'columns'],
          );
        }

        for (final (List<int>, List<double>, List<double>, List<double>)
            testCase
            in <(List<int>, List<double>, List<double>, List<double>)>[
              (
                <int>[2, 3],
                <double>[1, -2, 3, 4.5, -5.5, 6],
                <double>[10, 20, -30, 0.5, 5.5, -6],
                <double>[11, 18, -27, 5, 0, 0],
              ),
              (
                <int>[1, 4],
                <double>[-1, 0.25, 8, 100],
                <double>[1, 0.75, -3, -25],
                <double>[0, 1, 5, 75],
              ),
            ]) {
          final (
            List<int> shape,
            List<double> lhs,
            List<double> rhs,
            List<double> expected,
          ) = testCase;
          final OrtTensor lhsTensor = OrtTensor.fromFloat32List(
            runtime: runtime,
            values: Float32List.fromList(lhs),
            shape: shape,
          );
          final OrtTensor rhsTensor = OrtTensor.fromFloat32List(
            runtime: runtime,
            values: Float32List.fromList(rhs),
            shape: shape,
          );
          OrtRunResult? result;
          try {
            result = session.run(
              inputs: <String, OrtValue>{
                'lhs.matrix': lhsTensor,
                'rhs/matrix': rhsTensor,
              },
            );
            final OrtTensor output = result.tensor('sum.matrix');
            expect(output.shape.dimensions, shape);
            expect(output.copyFloat32Data(), expected);
          } finally {
            result?.dispose();
            rhsTensor.dispose();
            lhsTensor.dispose();
          }
        }
      } finally {
        session.dispose();
        runtime.dispose();
      }
    });

    test('dynamic MatMul accepts two rectangular shape combinations', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'dynamic_matmul.onnx');
      try {
        expect(session.inputs.map((OrtValueInfo value) => value.name), <String>[
          'left.matrix',
          'right/matrix',
        ]);
        expect(session.outputs.single.name, 'product.matrix');
        expect(
          session.inputs[0].type.dimensions.map(
            (OrtDimension dimension) => dimension.symbol,
          ),
          <String?>['rows', 'inner'],
        );
        expect(
          session.inputs[1].type.dimensions.map(
            (OrtDimension dimension) => dimension.symbol,
          ),
          <String?>['inner', 'columns'],
        );
        expect(
          session.outputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.symbol,
          ),
          <String?>['rows', 'columns'],
        );

        final testCases =
            <
              ({
                List<int> leftShape,
                List<int> rightShape,
                List<int> outputShape,
                List<double> left,
                List<double> right,
                List<double> expected,
              })
            >[
              (
                leftShape: <int>[2, 3],
                rightShape: <int>[3, 2],
                outputShape: <int>[2, 2],
                left: <double>[1, 2, 3, 4, 5, 6],
                right: <double>[7, 8, 9, 10, 11, 12],
                expected: <double>[58, 64, 139, 154],
              ),
              (
                leftShape: <int>[1, 2],
                rightShape: <int>[2, 3],
                outputShape: <int>[1, 3],
                left: <double>[-1, 2],
                right: <double>[3, 4, 5, -6, 7, 8],
                expected: <double>[-15, 10, 11],
              ),
            ];
        for (final testCase in testCases) {
          final OrtTensor left = OrtTensor.fromFloat32List(
            runtime: runtime,
            values: Float32List.fromList(testCase.left),
            shape: testCase.leftShape,
          );
          final OrtTensor right = OrtTensor.fromFloat32List(
            runtime: runtime,
            values: Float32List.fromList(testCase.right),
            shape: testCase.rightShape,
          );
          OrtRunResult? result;
          try {
            result = session.run(
              inputs: <String, OrtValue>{
                'left.matrix': left,
                'right/matrix': right,
              },
            );
            final OrtTensor output = result.tensor('product.matrix');
            expect(output.shape.dimensions, testCase.outputShape);
            expect(output.copyFloat32Data(), testCase.expected);
          } finally {
            result?.dispose();
            right.dispose();
            left.dispose();
          }
        }
      } finally {
        session.dispose();
        runtime.dispose();
      }
    });

    test(
      'zero-length tensor crosses a real run without fabricated storage',
      () {
        final OrtRuntime runtime = openRuntime();
        final OrtSession session = openSession(
          runtime,
          'zero_length_float32_identity.onnx',
        );
        final OrtTensor input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: Float32List(0),
          shape: const <int>[0, 3],
        );
        OrtRunResult? result;
        try {
          expect(
            session.inputs.single.type.dimensions.map(
              (OrtDimension dimension) => dimension.value,
            ),
            <int?>[0, 3],
          );
          result = session.run(
            inputs: <String, OrtValue>{'empty/input': input},
          );
          final OrtTensor output = result.tensor('empty.output');
          expect(output.shape.dimensions, <int>[0, 3]);
          expect(output.shape.elementCount, 0);
          expect(output.info.byteLength, 0);
          expect(output.copyFloat32Data(), isEmpty);
        } finally {
          result?.dispose();
          input.dispose();
          session.dispose();
          runtime.dispose();
        }
      },
    );

    test('bool identity returns only normalized logical values', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'bool_identity.onnx');
      const List<bool> callerValues = <bool>[
        false,
        true,
        true,
        false,
        true,
        false,
      ];
      const List<bool> expectedValues = <bool>[
        false,
        true,
        true,
        false,
        true,
        false,
      ];
      final OrtTensor input = OrtTensor.fromBoolList(
        runtime: runtime,
        values: callerValues,
        shape: const <int>[6],
      );
      OrtRunResult? result;
      try {
        result = session.run(inputs: <String, OrtValue>{'flags/input': input});
        final OrtTensor output = result.tensor('flags.normalized');
        expect(output.elementType, OrtTensorElementType.boolean);
        expect(output.copyBoolData(), expectedValues);
      } finally {
        result?.dispose();
        input.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('all ordinary integer widths preserve exact named outputs', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(
        runtime,
        'fixed_width_integer_identities.onnx',
      );
      final Int8List int8Caller = Int8List.fromList(<int>[-128, -1, 0, 127]);
      final Uint8List uint8Caller = Uint8List.fromList(<int>[0, 1, 254, 255]);
      final Int16List int16Caller = Int16List.fromList(<int>[
        -32768,
        -1,
        0,
        32767,
      ]);
      final Uint16List uint16Caller = Uint16List.fromList(<int>[
        0,
        1,
        65534,
        65535,
      ]);
      final Int32List int32Caller = Int32List.fromList(<int>[
        -2147483648,
        -1,
        0,
        2147483647,
      ]);
      final Uint32List uint32Caller = Uint32List.fromList(<int>[
        0,
        1,
        4294967294,
        4294967295,
      ]);
      final Int64List int64Caller = Int64List.fromList(<int>[
        -9223372036854775808,
        -1,
        0,
        9223372036854775807,
      ]);
      final Uint64List uint64Caller = Uint64List.fromList(<int>[
        0,
        1,
        4294967296,
        9223372036854775807,
      ]);
      final Int8List int8Expected = Int8List.fromList(<int>[-128, -1, 0, 127]);
      final Uint8List uint8Expected = Uint8List.fromList(<int>[0, 1, 254, 255]);
      final Int16List int16Expected = Int16List.fromList(<int>[
        -32768,
        -1,
        0,
        32767,
      ]);
      final Uint16List uint16Expected = Uint16List.fromList(<int>[
        0,
        1,
        65534,
        65535,
      ]);
      final Int32List int32Expected = Int32List.fromList(<int>[
        -2147483648,
        -1,
        0,
        2147483647,
      ]);
      final Uint32List uint32Expected = Uint32List.fromList(<int>[
        0,
        1,
        4294967294,
        4294967295,
      ]);
      final Int64List int64Expected = Int64List.fromList(<int>[
        -9223372036854775808,
        -1,
        0,
        9223372036854775807,
      ]);
      final Uint64List uint64Expected = Uint64List.fromList(<int>[
        0,
        1,
        4294967296,
        9223372036854775807,
      ]);
      final Map<String, OrtTensor> inputs = <String, OrtTensor>{
        'signed/int8.input': OrtTensor.fromInt8List(
          runtime: runtime,
          values: int8Caller,
          shape: const <int>[4],
        ),
        'unsigned/uint8.input': OrtTensor.fromUint8List(
          runtime: runtime,
          values: uint8Caller,
          shape: const <int>[4],
        ),
        'signed/int16.input': OrtTensor.fromInt16List(
          runtime: runtime,
          values: int16Caller,
          shape: const <int>[4],
        ),
        'unsigned/uint16.input': OrtTensor.fromUint16List(
          runtime: runtime,
          values: uint16Caller,
          shape: const <int>[4],
        ),
        'signed/int32.input': OrtTensor.fromInt32List(
          runtime: runtime,
          values: int32Caller,
          shape: const <int>[4],
        ),
        'unsigned/uint32.input': OrtTensor.fromUint32List(
          runtime: runtime,
          values: uint32Caller,
          shape: const <int>[4],
        ),
        'signed/int64.input': OrtTensor.fromInt64List(
          runtime: runtime,
          values: int64Caller,
          shape: const <int>[4],
        ),
        'unsigned/uint64.input': OrtTensor.fromUint64List(
          runtime: runtime,
          values: uint64Caller,
          shape: const <int>[4],
        ),
      };
      const List<String> requestedOutputs = <String>[
        'unsigned/uint64.output',
        'signed/int64.output',
        'unsigned/uint32.output',
        'signed/int32.output',
        'unsigned/uint16.output',
        'signed/int16.output',
        'unsigned/uint8.output',
        'signed/int8.output',
      ];
      OrtRunResult? result;
      try {
        expect(
          session.inputs.map((OrtValueInfo value) => value.name),
          inputs.keys,
        );
        expect(
          session.inputs.map(
            (OrtValueInfo value) => value.type.tensorElementType,
          ),
          <OrtTensorElementType>[
            OrtTensorElementType.int8,
            OrtTensorElementType.uint8,
            OrtTensorElementType.int16,
            OrtTensorElementType.uint16,
            OrtTensorElementType.int32,
            OrtTensorElementType.uint32,
            OrtTensorElementType.int64,
            OrtTensorElementType.uint64,
          ],
        );
        int8Caller.fillRange(0, int8Caller.length, 0);
        uint8Caller.fillRange(0, uint8Caller.length, 0);
        int16Caller.fillRange(0, int16Caller.length, 0);
        uint16Caller.fillRange(0, uint16Caller.length, 0);
        int32Caller.fillRange(0, int32Caller.length, 0);
        uint32Caller.fillRange(0, uint32Caller.length, 0);
        int64Caller.fillRange(0, int64Caller.length, 0);
        uint64Caller.fillRange(0, uint64Caller.length, 0);

        result = session.run(inputs: inputs, outputNames: requestedOutputs);
        expect(result.outputNames, requestedOutputs);
        expect(
          result.tensor('signed/int8.output').copyInt8Data(),
          int8Expected,
        );
        expect(
          result.tensor('unsigned/uint8.output').copyUint8Data(),
          uint8Expected,
        );
        expect(
          result.tensor('signed/int16.output').copyInt16Data(),
          int16Expected,
        );
        expect(
          result.tensor('unsigned/uint16.output').copyUint16Data(),
          uint16Expected,
        );
        expect(
          result.tensor('signed/int32.output').copyInt32Data(),
          int32Expected,
        );
        expect(
          result.tensor('unsigned/uint32.output').copyUint32Data(),
          uint32Expected,
        );
        expect(
          result.tensor('signed/int64.output').copyInt64Data(),
          int64Expected,
        );
        expect(
          result.tensor('unsigned/uint64.output').copyUint64Data(),
          uint64Expected,
        );
      } finally {
        result?.dispose();
        for (final OrtTensor input in inputs.values) {
          input.dispose();
        }
        session.dispose();
        runtime.dispose();
      }
    });

    test('sequence children can be retained independently of every parent', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(
        runtime,
        'sequence_construct.onnx',
      );
      final OrtTensor first = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[1, 2]),
        shape: const <int>[2],
      );
      final OrtTensor second = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[3, 4]),
        shape: const <int>[2],
      );
      OrtRunResult? result;
      OrtTensor? retainedChild;
      OrtSequence? constructed;
      try {
        constructed = OrtSequence.fromValues(
          runtime: runtime,
          values: <OrtValue>[first, second],
        );
        first.dispose();
        second.dispose();
        expect(
          (constructed.elements.first as OrtTensor).copyFloat32Data(),
          <double>[1, 2],
        );

        final OrtTensor runFirst = constructed.elements[0] as OrtTensor;
        final OrtTensor runSecond = constructed.elements[1] as OrtTensor;
        result = session.run(
          inputs: <String, OrtValue>{'first': runFirst, 'second': runSecond},
        );
        final OrtSequence output = result.value('values') as OrtSequence;
        retainedChild = output.elements[1].retain() as OrtTensor;
        output.dispose();
        result.dispose();
        constructed.dispose();
        session.dispose();
        runtime.dispose();
        expect(retainedChild.copyFloat32Data(), <double>[3, 4]);
      } finally {
        retainedChild?.dispose();
        result?.dispose();
        constructed?.dispose();
        second.dispose();
        first.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('sequence construction derives a common dynamic tensor shape', () {
      final OrtRuntime runtime = openRuntime();
      final OrtTensor first = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[1, 2]),
        shape: const <int>[2],
      );
      final OrtTensor second = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[3, 4, 5]),
        shape: const <int>[3],
      );
      OrtSequence? sequence;
      try {
        sequence = OrtSequence.fromValues(
          runtime: runtime,
          values: <OrtValue>[first, second],
        );
        final OrtTypeInfo elementType = sequence.type.sequenceElement!;
        expect(elementType.tensorElementType, OrtTensorElementType.float32);
        expect(elementType.hasShape, isTrue);
        expect(elementType.dimensions, hasLength(1));
        expect(elementType.dimensions.single.isDynamic, isTrue);
        expect((sequence.elements[0] as OrtTensor).copyFloat32Data(), <double>[
          1,
          2,
        ]);
        expect((sequence.elements[1] as OrtTensor).copyFloat32Data(), <double>[
          3,
          4,
          5,
        ]);
      } finally {
        sequence?.dispose();
        second.dispose();
        first.dispose();
        runtime.dispose();
      }
    });

    test('optional None, Some, omitted, and raw contained inputs agree', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(
        runtime,
        'optional_has_element.onnx',
      );
      final OrtTensor tensor = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[5, 6]),
        shape: const <int>[2],
      );
      final OrtTypeInfo elementType =
          session.inputs.single.type.optionalElement!;
      final OrtOptional none = OrtOptional.none(
        runtime: runtime,
        elementType: elementType,
      );
      final OrtOptional some = OrtOptional.some(value: tensor);
      try {
        for (final (Map<String, OrtValue> inputs, bool expected)
            in <(Map<String, OrtValue>, bool)>[
              (<String, OrtValue>{}, false),
              (<String, OrtValue>{'maybe_value': none}, false),
              (<String, OrtValue>{'maybe_value': tensor}, true),
              (<String, OrtValue>{'maybe_value': some}, true),
            ]) {
          final OrtRunResult result = session.run(inputs: inputs);
          try {
            expect(result.tensor('has_value').copyBoolData(), <bool>[expected]);
          } finally {
            result.dispose();
          }
        }
      } finally {
        some.dispose();
        none.dispose();
        tensor.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('ZipMap key and value leaves outlive map, sequence, and result', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'zipmap_string.onnx');
      final OrtTensor input = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[0.25, 0.75]),
        shape: const <int>[1, 2],
      );
      OrtRunResult? result;
      OrtStringTensor? retainedKeys;
      OrtTensor? retainedValues;
      OrtMap? constructed;
      try {
        result = session.run(
          inputs: <String, OrtValue>{'probabilities': input},
        );
        final OrtSequence sequence = result.value('scores') as OrtSequence;
        final OrtMap map = sequence.elements.single as OrtMap;
        retainedKeys = map.keys.retain() as OrtStringTensor;
        retainedValues = map.values.retain() as OrtTensor;
        constructed = OrtMap.fromValues(
          runtime: runtime,
          keys: map.keys,
          values: map.values,
        );

        map.dispose();
        sequence.dispose();
        result.dispose();
        session.dispose();
        runtime.dispose();
        expect(retainedKeys.copyStrings(), <String>['cat', 'dog']);
        expect(retainedValues.copyFloat32Data(), <double>[0.25, 0.75]);
        expect((constructed.keys as OrtStringTensor).copyStrings(), <String>[
          'cat',
          'dog',
        ]);
      } finally {
        constructed?.dispose();
        retainedValues?.dispose();
        retainedKeys?.dispose();
        result?.dispose();
        input.dispose();
        session.dispose();
        runtime.dispose();
      }
    });

    test('map construction rejects invalid shapes before native execution', () {
      final OrtRuntime runtime = openRuntime();
      final OrtTensor rankTwoKeys = OrtTensor.fromInt64List(
        runtime: runtime,
        values: Int64List.fromList(<int>[1, 2]),
        shape: const <int>[1, 2],
      );
      final OrtTensor rankOneKeys = OrtTensor.fromInt64List(
        runtime: runtime,
        values: Int64List.fromList(<int>[1, 2]),
        shape: const <int>[2],
      );
      final OrtTensor wrongCardinality = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[1]),
        shape: const <int>[1],
      );
      try {
        expect(
          () => OrtMap.fromValues(
            runtime: runtime,
            keys: rankTwoKeys,
            values: wrongCardinality,
          ),
          throwsArgumentError,
        );
        expect(
          () => OrtMap.fromValues(
            runtime: runtime,
            keys: rankOneKeys,
            values: wrongCardinality,
          ),
          throwsArgumentError,
        );
      } finally {
        wrongCardinality.dispose();
        rankOneKeys.dispose();
        rankTwoKeys.dispose();
        runtime.dispose();
      }
    });

    test('leased TypedData survives result, tensor, session, and runtime', () {
      final OrtRuntime runtime = openRuntime();
      final OrtSession session = openSession(runtime, 'float16_identity.onnx');
      final Uint16List bits = Uint16List.fromList(<int>[
        0x0000,
        0x8000,
        0x3c00,
        0x0001,
        0x0400,
        0x7c00,
        0x7e00,
      ]);
      final OrtTensor input = OrtTensor.fromFloat16Bits(
        runtime: runtime,
        bits: bits,
        shape: const <int>[7],
      );
      final OrtRunResult result = session.run(
        inputs: <String, OrtValue>{'float16_input': input},
      );
      final OrtTensor output = result.tensor('float16_output');
      final Uint16List alias = _float16BufferAlias(output);
      expect(() => alias[0] = 1, throwsUnsupportedError);

      output.dispose();
      result.dispose();
      input.dispose();
      session.dispose();
      runtime.dispose();
      expect(alias, bits);
    });

    test('buffer view is mutable and survives buffer/runtime disposal', () {
      final OrtRuntime runtime = openRuntime();
      final OrtNativeBuffer buffer = OrtNativeBuffer.allocate(
        runtime: runtime,
        byteLength: 4,
      );
      final Uint8List alias = _bufferAlias(buffer);
      alias.setAll(0, <int>[1, 2, 3, 4]);
      expect(alias, <int>[1, 2, 3, 4]);
      buffer.dispose();
      runtime.dispose();
      alias[3] = 9;
      expect(alias, <int>[1, 2, 3, 9]);
      buffer.dispose();
      runtime.dispose();
    });

    test('tensor data lease finalizer releases exactly once', () async {
      final FonixNativeApi nativeApi = FonixNativeApi.nativeAsset();
      final OrtRuntime runtime = openRuntimeWithApi(
        nativeApi,
        'fonix-phase3-tensor-lease',
      );
      final Uint16List bits = Uint16List.fromList(<int>[
        0x0000,
        0x8000,
        0x3c00,
        0x7c00,
      ]);
      final OrtTensor tensor = OrtTensor.fromFloat16Bits(
        runtime: runtime,
        bits: bits,
        shape: const <int>[4],
      );
      final int baseline = nativeApi.dataLeaseReleaseCountForTesting;
      final WeakReference<Uint16List> weakAlias =
          await _holdTensorAliasAcrossGc(
            tensor: tensor,
            expected: bits,
            nativeApi: nativeApi,
            baseline: baseline,
          );
      runtime.dispose();

      await _driveGcUntil(
        () => nativeApi.dataLeaseReleaseCountForTesting == baseline + 1,
      );
      expect(weakAlias.target, isNull);
      await _forceGcCycle();
      await _forceGcCycle();
      expect(nativeApi.dataLeaseReleaseCountForTesting, baseline + 1);
    });

    test('buffer data lease finalizer releases exactly once', () async {
      final FonixNativeApi nativeApi = FonixNativeApi.nativeAsset();
      final OrtRuntime runtime = openRuntimeWithApi(
        nativeApi,
        'fonix-phase3-buffer-lease',
      );
      final OrtNativeBuffer buffer = OrtNativeBuffer.allocate(
        runtime: runtime,
        byteLength: 4,
      );
      final int baseline = nativeApi.dataLeaseReleaseCountForTesting;
      final WeakReference<Uint8List> weakAlias = await _holdBufferAliasAcrossGc(
        buffer: buffer,
        nativeApi: nativeApi,
        baseline: baseline,
      );
      runtime.dispose();

      await _driveGcUntil(
        () => nativeApi.dataLeaseReleaseCountForTesting == baseline + 1,
      );
      expect(weakAlias.target, isNull);
      await _forceGcCycle();
      await _forceGcCycle();
      expect(nativeApi.dataLeaseReleaseCountForTesting, baseline + 1);
    });

    test('external bytes and copied model metadata remain deterministic', () {
      final OrtRuntime runtime = openRuntime();
      final File model = fixture('external_data/valid/model.onnx');
      final File weights = fixture('external_data/valid/weights.bin');
      final OrtModelSource source = OrtModelSource.bytesWithExternalData(
        model.readAsBytesSync(),
        externalData: <String, Uint8List>{
          'weights.bin': weights.readAsBytesSync(),
        },
      );
      final OrtSession external = OrtSession.fromModel(
        runtime: runtime,
        model: source,
      );
      final OrtTensor input = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List.fromList(<double>[10, 10, 10, 10]),
        shape: const <int>[4],
      );
      final OrtRunResult result = external.run(
        inputs: <String, OrtValue>{'input': input},
      );
      expect(result.tensor('output').copyFloat32Data(), <double>[
        10,
        20,
        30,
        40,
      ]);

      final OrtSession metadata = openSession(
        runtime,
        'metadata_identity.onnx',
      );
      final OrtModelMetadata copied = metadata.modelMetadata;
      expect(copied.producerName, 'fonix-fixtures');
      expect(copied.graphName, 'メタデータグラフ');
      expect(copied.domain, 'dev.fonix.fixtures.metadata');
      expect(copied.version, 42);
      expect(copied.custom.keys, <String>['empty', 'purpose', 'unicode']);
      metadata.dispose();
      expect(copied.custom['unicode'], 'こんにちは🌿');
      expect(metadata.modelMetadata, same(copied));

      result.dispose();
      input.dispose();
      external.dispose();
      runtime.dispose();
    });

    test('same-adapter values are still bound to one runtime owner', () {
      final FonixNativeApi nativeApi = FonixNativeApi.nativeAsset();
      final OrtRuntimeSource source = OrtRuntimeSource.file(
        absolutePath: p.normalize(runtimePath),
        allowedRoot: p.dirname(p.normalize(runtimePath)),
      );
      final OrtRuntime firstRuntime = openOrtRuntimeWithNativeApiForTesting(
        nativeApi: nativeApi,
        source: source,
        logId: 'fonix-phase3-owner-a',
      );
      final OrtRuntime secondRuntime = openOrtRuntimeWithNativeApiForTesting(
        nativeApi: nativeApi,
        source: source,
        logId: 'fonix-phase3-owner-b',
      );
      final OrtSession session = openSession(
        firstRuntime,
        'float16_identity.onnx',
      );
      final OrtTensor firstInput = OrtTensor.fromFloat16Bits(
        runtime: firstRuntime,
        bits: Uint16List(7),
        shape: const <int>[7],
      );
      final OrtTensor secondInput = OrtTensor.fromFloat16Bits(
        runtime: secondRuntime,
        bits: Uint16List(7),
        shape: const <int>[7],
      );
      final OrtRunOptions secondOptions = OrtRunOptions(runtime: secondRuntime);
      try {
        expect(
          () => OrtSequence.fromValues(
            runtime: firstRuntime,
            values: <OrtValue>[secondInput],
          ),
          throwsArgumentError,
        );
        expect(
          () => session.run(
            inputs: <String, OrtValue>{'float16_input': secondInput},
          ),
          throwsArgumentError,
        );
        expect(
          () => session.run(
            inputs: <String, OrtValue>{'float16_input': firstInput},
            runOptions: secondOptions,
          ),
          throwsArgumentError,
        );
        expect(
          () => session.run(
            inputs: <String, OrtValue>{'float16_input': firstInput},
            outputNames: List<String>.filled(257, 'float16_output'),
          ),
          throwsArgumentError,
        );
      } finally {
        secondOptions.dispose();
        secondInput.dispose();
        firstInput.dispose();
        session.dispose();
        secondRuntime.dispose();
        firstRuntime.dispose();
      }
    });
  });
}

Uint16List _float16BufferAlias(OrtTensor tensor) {
  final Uint16List view = tensor.viewFloat16Bits();
  return view.buffer.asUint16List(view.offsetInBytes, view.length);
}

Uint8List _bufferAlias(OrtNativeBuffer buffer) {
  final Uint8List view = buffer.viewBytes();
  return view.buffer.asUint8List(view.offsetInBytes, view.length);
}

Future<WeakReference<Uint16List>> _holdTensorAliasAcrossGc({
  required OrtTensor tensor,
  required Uint16List expected,
  required FonixNativeApi nativeApi,
  required int baseline,
}) async {
  final Uint16List alias = _float16BufferAlias(tensor);
  tensor.dispose();
  await _forceGcCycle();
  expect(nativeApi.dataLeaseReleaseCountForTesting, baseline);
  expect(alias, expected);
  return WeakReference<Uint16List>(alias);
}

Future<WeakReference<Uint8List>> _holdBufferAliasAcrossGc({
  required OrtNativeBuffer buffer,
  required FonixNativeApi nativeApi,
  required int baseline,
}) async {
  final Uint8List alias = _bufferAlias(buffer);
  alias.setAll(0, const <int>[1, 2, 3, 4]);
  buffer.dispose();
  await _forceGcCycle();
  expect(nativeApi.dataLeaseReleaseCountForTesting, baseline);
  expect(alias, const <int>[1, 2, 3, 4]);
  return WeakReference<Uint8List>(alias);
}

Future<void> _driveGcUntil(bool Function() condition) async {
  for (var attempt = 0; attempt < 8; attempt += 1) {
    if (condition()) return;
    await _forceGcCycle();
  }
  if (!condition()) {
    throw StateError(
      'The expected finalizer did not run after eight GC cycles.',
    );
  }
}

Future<void> _forceGcCycle() async {
  final Completer<void> collected = Completer<void>();
  final Finalizer<int> finalizer = Finalizer<int>((int _) {
    if (!collected.isCompleted) collected.complete();
  });
  _attachShortLivedGcTarget(finalizer);
  for (var attempt = 0; attempt < 128 && !collected.isCompleted; attempt += 1) {
    final List<Uint8List> pressure = List<Uint8List>.generate(
      4,
      (int _) => Uint8List(1024 * 1024),
      growable: false,
    );
    pressure.first[0] = attempt & 0xff;
    await Future<void>.delayed(const Duration(milliseconds: 1));
  }
  await collected.future.timeout(const Duration(seconds: 10));
  await Future<void>.delayed(Duration.zero);
}

@pragma('vm:never-inline')
void _attachShortLivedGcTarget(Finalizer<int> finalizer) {
  final _GcTarget target = _GcTarget();
  finalizer.attach(target, 1);
}

final class _GcTarget {}
