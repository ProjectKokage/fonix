import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:test/test.dart';

void main() {
  group('IEEE binary16 codec', () {
    test('encodes exact values, boundaries, and ties-to-even', () {
      expect(OrtFloat16Codec.encode(0.0), 0x0000);
      expect(OrtFloat16Codec.encode(-0.0), 0x8000);
      expect(OrtFloat16Codec.encode(1.0), 0x3c00);
      expect(OrtFloat16Codec.encode(-2.0), 0xc000);
      expect(OrtFloat16Codec.encode(65504.0), 0x7bff);
      expect(OrtFloat16Codec.encode(65519.0), 0x7bff);
      expect(OrtFloat16Codec.encode(65520.0), 0x7c00);
      expect(OrtFloat16Codec.encode(double.infinity), 0x7c00);
      expect(OrtFloat16Codec.encode(double.negativeInfinity), 0xfc00);

      // Halfway from fraction 0 to 1 keeps the even zero fraction.
      expect(OrtFloat16Codec.encode(1.00048828125), 0x3c00);
      // Halfway from fraction 1 to 2 rounds to even fraction 2.
      expect(OrtFloat16Codec.encode(1.00146484375), 0x3c02);

      final double minimumSubnormal = OrtFloat16Codec.decode(0x0001);
      expect(OrtFloat16Codec.encode(minimumSubnormal), 0x0001);
      expect(OrtFloat16Codec.encode(minimumSubnormal * 0.5), 0x0000);
      expect(OrtFloat16Codec.encode(minimumSubnormal * 1.5), 0x0002);
      expect(OrtFloat16Codec.decode(0x0400), 0.00006103515625);
      expect(OrtFloat16Codec.decode(0x03ff), lessThan(0.00006103515625));

      // Signaling NaNs are quieted; sign and available payload bits survive.
      expect(OrtFloat16Codec.encode(OrtFloat16Codec.decode(0x7c01)), 0x7e01);
      expect(OrtFloat16Codec.encode(OrtFloat16Codec.decode(0xfc01)), 0xfe01);
      expect(OrtFloat16Codec.encode(OrtFloat16Codec.decode(0x7e55)), 0x7e55);
    });

    test('exhaustively round-trips every non-NaN raw pattern', () {
      for (var bits = 0; bits <= 0xffff; bits += 1) {
        final bool isNaN = (bits & 0x7c00) == 0x7c00 && (bits & 0x03ff) != 0;
        final double decoded = OrtFloat16Codec.decode(bits);
        if (isNaN) {
          if (!decoded.isNaN) {
            fail('binary16 0x${bits.toRadixString(16)} did not decode as NaN');
          }
        } else {
          final int encoded = OrtFloat16Codec.encode(decoded);
          if (encoded != bits) {
            fail(
              'binary16 0x${bits.toRadixString(16)} round-tripped as '
              '0x${encoded.toRadixString(16)}',
            );
          }
        }
      }
    });

    test('bulk conversion copies storage and observes configured limits', () {
      final Float32List values = Float32List.fromList(<double>[1, -2, 0]);
      final Uint16List bits = OrtFloat16Codec.encodeFloat32List(values);
      expect(bits, <int>[0x3c00, 0xc000, 0x0000]);
      values[0] = 9;
      expect(bits.first, 0x3c00);

      final Float32List decoded = OrtFloat16Codec.decodeFloat32List(bits);
      bits[0] = 0;
      expect(decoded, <double>[1, -2, 0]);
      expect(
        () => OrtFloat16Codec.encodeFloat32List(
          Float32List(3),
          limits: OrtResourceLimits(maxTensorElements: 2),
        ),
        throwsRangeError,
      );
    });
  });

  group('bfloat16 codec', () {
    test('encodes exact values, boundaries, and ties-to-even', () {
      expect(OrtBFloat16Codec.encode(0.0), 0x0000);
      expect(OrtBFloat16Codec.encode(-0.0), 0x8000);
      expect(OrtBFloat16Codec.encode(1.0), 0x3f80);
      expect(OrtBFloat16Codec.encode(-2.0), 0xc000);
      expect(OrtBFloat16Codec.encode(double.infinity), 0x7f80);
      expect(OrtBFloat16Codec.encode(double.negativeInfinity), 0xff80);

      // Halfway from fraction 0 to 1 keeps the even zero fraction.
      expect(OrtBFloat16Codec.encode(1.00390625), 0x3f80);
      // Halfway from fraction 1 to 2 rounds to even fraction 2.
      expect(OrtBFloat16Codec.encode(1.01171875), 0x3f82);

      final double maximumFinite = OrtBFloat16Codec.decode(0x7f7f);
      expect(OrtBFloat16Codec.encode(maximumFinite), 0x7f7f);
      final double minimumSubnormal = OrtBFloat16Codec.decode(0x0001);
      expect(OrtBFloat16Codec.encode(minimumSubnormal), 0x0001);
      expect(OrtBFloat16Codec.encode(minimumSubnormal * 0.5), 0x0000);
      expect(OrtBFloat16Codec.encode(minimumSubnormal * 1.5), 0x0002);
      expect(OrtBFloat16Codec.encode(OrtBFloat16Codec.decode(0x7f81)), 0x7fc1);
      expect(OrtBFloat16Codec.encode(OrtBFloat16Codec.decode(0xff81)), 0xffc1);
    });

    test('exhaustively round-trips every non-NaN raw pattern', () {
      for (var bits = 0; bits <= 0xffff; bits += 1) {
        final bool isNaN = (bits & 0x7f80) == 0x7f80 && (bits & 0x007f) != 0;
        final double decoded = OrtBFloat16Codec.decode(bits);
        if (isNaN) {
          if (!decoded.isNaN) {
            fail('bfloat16 0x${bits.toRadixString(16)} did not decode as NaN');
          }
        } else {
          final int encoded = OrtBFloat16Codec.encode(decoded);
          if (encoded != bits) {
            fail(
              'bfloat16 0x${bits.toRadixString(16)} round-tripped as '
              '0x${encoded.toRadixString(16)}',
            );
          }
        }
      }
    });

    test('bulk conversion copies storage and rejects invalid raw words', () {
      final Float32List values = Float32List.fromList(<double>[1, -2, 0]);
      final Uint16List bits = OrtBFloat16Codec.encodeFloat32List(values);
      expect(bits, <int>[0x3f80, 0xc000, 0x0000]);
      values[0] = 9;
      expect(bits.first, 0x3f80);

      final Float32List decoded = OrtBFloat16Codec.decodeFloat32List(bits);
      bits[0] = 0;
      expect(decoded, <double>[1, -2, 0]);
      expect(() => OrtBFloat16Codec.decode(-1), throwsRangeError);
      expect(() => OrtBFloat16Codec.decode(0x10000), throwsRangeError);
    });
  });
}
