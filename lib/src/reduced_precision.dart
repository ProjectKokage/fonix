import 'dart:typed_data';

import 'resource_limits.dart';

/// IEEE 754 binary16 conversion using raw [Uint16List] storage.
///
/// Scalar encoding converts binary64 directly with round-to-nearest,
/// ties-to-even. It does not pass through binary32, avoiding double rounding.
/// NaNs are quieted while preserving the sign and available high payload bits.
abstract final class OrtFloat16Codec {
  static int encode(double value) => _encodeReducedFloat(
    value,
    exponentBits: 5,
    fractionBits: 10,
    exponentBias: 15,
  );

  static double decode(int bits) => _decodeReducedFloat(
    _checkedBits(bits),
    exponentBits: 5,
    fractionBits: 10,
    exponentBias: 15,
  );

  /// Copies binary32 values into raw IEEE binary16 storage.
  static Uint16List encodeFloat32List(
    Float32List values, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _checkStorageLength(values.length, limits);
    final Uint16List output = Uint16List(values.length);
    for (var index = 0; index < values.length; index += 1) {
      output[index] = encode(values[index]);
    }
    return output;
  }

  /// Copies raw IEEE binary16 storage into exactly representable binary32s.
  static Float32List decodeFloat32List(
    Uint16List bits, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _checkStorageLength(bits.length, limits);
    final Float32List output = Float32List(bits.length);
    for (var index = 0; index < bits.length; index += 1) {
      output[index] = decode(bits[index]);
    }
    return output;
  }
}

/// Brain floating-point conversion using raw [Uint16List] storage.
///
/// The format has one sign bit, eight exponent bits, and seven fraction bits.
/// Scalar encoding converts binary64 directly with round-to-nearest,
/// ties-to-even. NaNs are quieted while preserving the sign and available high
/// payload bits.
abstract final class OrtBFloat16Codec {
  static int encode(double value) => _encodeReducedFloat(
    value,
    exponentBits: 8,
    fractionBits: 7,
    exponentBias: 127,
  );

  static double decode(int bits) => _decodeReducedFloat(
    _checkedBits(bits),
    exponentBits: 8,
    fractionBits: 7,
    exponentBias: 127,
  );

  /// Copies binary32 values into raw bfloat16 storage.
  static Uint16List encodeFloat32List(
    Float32List values, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _checkStorageLength(values.length, limits);
    final Uint16List output = Uint16List(values.length);
    for (var index = 0; index < values.length; index += 1) {
      output[index] = encode(values[index]);
    }
    return output;
  }

  /// Copies raw bfloat16 storage into exactly representable binary32s.
  static Float32List decodeFloat32List(
    Uint16List bits, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _checkStorageLength(bits.length, limits);
    final Float32List output = Float32List(bits.length);
    for (var index = 0; index < bits.length; index += 1) {
      output[index] = decode(bits[index]);
    }
    return output;
  }
}

int _checkedBits(int bits) {
  if (bits < 0 || bits > 0xffff) {
    throw RangeError.range(bits, 0, 0xffff, 'bits');
  }
  return bits;
}

void _checkStorageLength(int length, OrtResourceLimits limits) {
  OrtShape(<int>[length], limits: limits).requiredBytes(2);
}

int _encodeReducedFloat(
  double value, {
  required int exponentBits,
  required int fractionBits,
  required int exponentBias,
}) {
  final ByteData data = ByteData(8)..setFloat64(0, value, Endian.big);
  final int high = data.getUint32(0, Endian.big);
  final int low = data.getUint32(4, Endian.big);
  final int sign = (high >>> 31) << 15;
  final int sourceExponent = (high >>> 20) & 0x7ff;
  final int sourceFraction = ((high & 0xfffff) << 32) | low;
  final int maximumExponentField = (1 << exponentBits) - 1;
  final int outputInfinity = maximumExponentField << fractionBits;

  if (sourceExponent == 0x7ff) {
    if (sourceFraction == 0) {
      return sign | outputInfinity;
    }
    final int payload =
        ((sourceFraction >> (52 - fractionBits)) | (1 << (fractionBits - 1))) &
        ((1 << fractionBits) - 1);
    return sign | outputInfinity | payload;
  }
  if (sourceExponent == 0 && sourceFraction == 0) {
    return sign;
  }

  final int significand;
  final int binaryExponent;
  if (sourceExponent == 0) {
    significand = sourceFraction;
    binaryExponent = -1022 - 52;
  } else {
    significand = (1 << 52) | sourceFraction;
    binaryExponent = sourceExponent - 1023 - 52;
  }

  var topExponent = binaryExponent + significand.bitLength - 1;
  final int minimumNormalExponent = 1 - exponentBias;
  final int maximumNormalExponent = maximumExponentField - 1 - exponentBias;
  if (topExponent > maximumNormalExponent) {
    return sign | outputInfinity;
  }

  if (topExponent >= minimumNormalExponent) {
    final int targetSignificandBits = fractionBits + 1;
    var rounded = _roundRightToEven(
      significand,
      significand.bitLength - targetSignificandBits,
    );
    if (rounded == 1 << targetSignificandBits) {
      rounded >>= 1;
      topExponent += 1;
      if (topExponent > maximumNormalExponent) {
        return sign | outputInfinity;
      }
    }
    final int exponentField = topExponent + exponentBias;
    final int fraction = rounded - (1 << fractionBits);
    return sign | (exponentField << fractionBits) | fraction;
  }

  final int subnormalUnitExponent = minimumNormalExponent - fractionBits;
  final int scale = binaryExponent - subnormalUnitExponent;
  final int rounded = scale >= 0
      ? significand << scale
      : _roundRightToEven(significand, -scale);
  if (rounded == 0) {
    return sign;
  }
  if (rounded >= 1 << fractionBits) {
    return sign | (1 << fractionBits);
  }
  return sign | rounded;
}

double _decodeReducedFloat(
  int bits, {
  required int exponentBits,
  required int fractionBits,
  required int exponentBias,
}) {
  final int sign = bits >>> 15;
  final int maximumExponentField = (1 << exponentBits) - 1;
  final int exponentMask = maximumExponentField << fractionBits;
  final int fractionMask = (1 << fractionBits) - 1;
  final int exponentField = (bits & exponentMask) >>> fractionBits;
  final int fraction = bits & fractionMask;
  if (exponentField == 0 && fraction == 0) {
    return _doubleFromParts(sign: sign, exponentField: 0, fraction: 0);
  }
  if (exponentField == maximumExponentField) {
    final int doubleFraction = fraction << (52 - fractionBits);
    return _doubleFromParts(
      sign: sign,
      exponentField: 0x7ff,
      fraction: doubleFraction,
    );
  }

  final int doubleExponent;
  final int doubleFraction;
  if (exponentField != 0) {
    doubleExponent = exponentField - exponentBias + 1023;
    doubleFraction = fraction << (52 - fractionBits);
  } else {
    final int leadingBit = fraction.bitLength - 1;
    final int topExponent = 1 - exponentBias - fractionBits + leadingBit;
    doubleExponent = topExponent + 1023;
    doubleFraction = (fraction - (1 << leadingBit)) << (52 - leadingBit);
  }
  return _doubleFromParts(
    sign: sign,
    exponentField: doubleExponent,
    fraction: doubleFraction,
  );
}

double _doubleFromParts({
  required int sign,
  required int exponentField,
  required int fraction,
}) {
  final ByteData data = ByteData(8);
  data
    ..setUint32(
      0,
      (sign << 31) | (exponentField << 20) | (fraction >>> 32),
      Endian.big,
    )
    ..setUint32(4, fraction & 0xffffffff, Endian.big);
  return data.getFloat64(0, Endian.big);
}

int _roundRightToEven(int value, int shift) {
  if (shift <= 0) {
    return value << -shift;
  }
  final int bitLength = value.bitLength;
  if (shift > bitLength) {
    return 0;
  }
  final int quotient = value >> shift;
  final int remainder = value - (quotient << shift);
  final int halfway = 1 << (shift - 1);
  if (remainder > halfway || (remainder == halfway && quotient.isOdd)) {
    return quotient + 1;
  }
  return quotient;
}
