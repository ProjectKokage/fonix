import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:test/test.dart';

import 'src/isolate_values.dart';

void main() {
  group('pointer-free isolate values', () {
    test('copies typed input and preserves composite output structure', () {
      final Float32List caller = Float32List.fromList(<double>[1, 2, 3]);
      final OrtIsolateTensor tensor = OrtIsolateTensor.fromFloat32List(
        values: caller,
        shape: const <int>[3],
      );
      caller.fillRange(0, caller.length, 99);
      expect(tensor.copyFloat32Data(), <double>[1, 2, 3]);

      final OrtIsolateValue composite = OrtIsolateSequence(<OrtIsolateValue>[
        tensor,
        float32IsolateTensor(<double>[4, 5, 6]),
      ]);
      expect(composite.kind, OrtValueKind.sequence);

      final OrtIsolateOptional absent = OrtIsolateOptional.none(
        elementType: OrtTypeInfo.sequence(
          OrtTypeInfo.tensor(
            elementType: OrtTensorElementType.string,
            hasShape: true,
            dimensions: <OrtDimension>[OrtDimension.fixed(2)],
          ),
        ),
      );
      expect(absent.value, isNull);
      expect(absent.type.optionalElement, absent.elementType);
    });

    test('validates exact bytes, booleans, strings, and map cardinality', () {
      expect(
        () => OrtIsolateTensor.fromBytes(
          elementType: OrtTensorElementType.float32,
          bytes: Uint8List(3),
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateTensor.fromBytes(
          elementType: OrtTensorElementType.boolean,
          bytes: Uint8List.fromList(<int>[2]),
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateTensor.fromStrings(
          values: const <String>['bad\u0000value'],
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateMap(
          keys: OrtIsolateTensor.fromInt64List(
            values: Int64List.fromList(<int>[1, 2]),
            shape: const <int>[2],
          ),
          values: float32IsolateTensor(<double>[1]),
        ),
        throwsArgumentError,
      );
    });

    test('bounds lazy sequence elements before materializing them', () {
      final OrtIsolateTensor element = float32IsolateTensor(<double>[1]);
      final OrtIsolateSequence maximum = OrtIsolateSequence(
        List<OrtIsolateValue>.filled(1024, element),
      );
      expect(maximum.elements, hasLength(1024));

      var observations = 0;
      Iterable<OrtIsolateValue> endlessElements() sync* {
        while (true) {
          observations += 1;
          yield element;
        }
      }

      expect(() => OrtIsolateSequence(endlessElements()), throwsRangeError);
      expect(observations, 1025);
    });

    test('rejects oversized string shapes before reading a lazy generator', () {
      var observations = 0;
      Iterable<String> unreadableStrings() sync* {
        while (true) {
          observations += 1;
          yield '';
        }
      }

      expect(
        () => OrtIsolateTensor.fromStrings(
          values: unreadableStrings(),
          shape: const <int>[1024 * 1024 + 1],
        ),
        throwsRangeError,
      );
      expect(observations, 0);
    });
  });
}
