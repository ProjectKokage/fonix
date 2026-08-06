/// Application-enforceable bounds applied before native calls.
final class OrtResourceLimits {
  factory OrtResourceLimits({
    int maxModelBytes = 512 * 1024 * 1024,
    int maxTensorBytes = 1024 * 1024 * 1024,
    int maxTensorElements = 1 << 30,
    int maxRank = 32,
    int maxDimension = 1 << 30,
    int maxProviders = 16,
    int maxProviderOptions = 64,
    int maxConfigEntries = 128,
    int maxDiagnosticsBytes = 1024 * 1024,
    int maxTypeDepth = 8,
    int maxTypeNodes = 4096,
  }) {
    _bounded(maxModelBytes, 2 * 1024 * 1024 * 1024, 'maxModelBytes');
    _bounded(maxTensorBytes, 1024 * 1024 * 1024, 'maxTensorBytes');
    _bounded(maxTensorElements, 1024 * 1024 * 1024, 'maxTensorElements');
    _bounded(maxRank, 32, 'maxRank');
    _bounded(maxDimension, 1024 * 1024 * 1024, 'maxDimension');
    _bounded(maxProviders, 16, 'maxProviders');
    _bounded(maxProviderOptions, 128, 'maxProviderOptions');
    _bounded(maxConfigEntries, 128, 'maxConfigEntries');
    _bounded(maxDiagnosticsBytes, 1024 * 1024, 'maxDiagnosticsBytes');
    _bounded(maxTypeDepth, 8, 'maxTypeDepth');
    _bounded(maxTypeNodes, 4096, 'maxTypeNodes');
    return OrtResourceLimits._(
      maxModelBytes: maxModelBytes,
      maxTensorBytes: maxTensorBytes,
      maxTensorElements: maxTensorElements,
      maxRank: maxRank,
      maxDimension: maxDimension,
      maxProviders: maxProviders,
      maxProviderOptions: maxProviderOptions,
      maxConfigEntries: maxConfigEntries,
      maxDiagnosticsBytes: maxDiagnosticsBytes,
      maxTypeDepth: maxTypeDepth,
      maxTypeNodes: maxTypeNodes,
    );
  }

  const OrtResourceLimits._({
    required this.maxModelBytes,
    required this.maxTensorBytes,
    required this.maxTensorElements,
    required this.maxRank,
    required this.maxDimension,
    required this.maxProviders,
    required this.maxProviderOptions,
    required this.maxConfigEntries,
    required this.maxDiagnosticsBytes,
    required this.maxTypeDepth,
    required this.maxTypeNodes,
  });

  /// Conservative defaults used by the stable API.
  static const OrtResourceLimits defaults = OrtResourceLimits._(
    maxModelBytes: 512 * 1024 * 1024,
    maxTensorBytes: 1024 * 1024 * 1024,
    maxTensorElements: 1 << 30,
    maxRank: 32,
    maxDimension: 1 << 30,
    maxProviders: 16,
    maxProviderOptions: 64,
    maxConfigEntries: 128,
    maxDiagnosticsBytes: 1024 * 1024,
    maxTypeDepth: 8,
    maxTypeNodes: 4096,
  );

  final int maxModelBytes;
  final int maxTensorBytes;
  final int maxTensorElements;
  final int maxRank;
  final int maxDimension;
  final int maxProviders;
  final int maxProviderOptions;
  final int maxConfigEntries;
  final int maxDiagnosticsBytes;
  final int maxTypeDepth;
  final int maxTypeNodes;

  static void _bounded(int value, int maximum, String name) {
    if (value <= 0 || value > maximum) {
      throw RangeError.range(value, 1, maximum, name);
    }
  }
}

/// A concrete tensor shape with checked element-count arithmetic.
final class OrtShape {
  factory OrtShape(
    Iterable<int> dimensions, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final List<int> values = <int>[];
    var count = 1;
    for (final int dimension in dimensions) {
      if (values.length == limits.maxRank) {
        throw RangeError('Tensor rank exceeds the configured limit.');
      }
      if (dimension < 0 || dimension > limits.maxDimension) {
        throw RangeError.range(dimension, 0, limits.maxDimension, 'dimension');
      }
      values.add(dimension);
      if (dimension == 0) {
        count = 0;
        continue;
      }
      if (count > limits.maxTensorElements ~/ dimension) {
        throw RangeError('Tensor element count exceeds the configured limit.');
      }
      count *= dimension;
    }
    return OrtShape._(List<int>.unmodifiable(values), count, limits);
  }

  const OrtShape._(this.dimensions, this.elementCount, this._limits);

  final List<int> dimensions;
  final int elementCount;
  final OrtResourceLimits _limits;

  int get rank => dimensions.length;
  bool get isScalar => dimensions.isEmpty;

  /// Computes the exact fixed-width storage size without integer overflow.
  int requiredBytes(int bytesPerElement) {
    if (bytesPerElement <= 0) {
      throw RangeError.value(bytesPerElement, 'bytesPerElement');
    }
    if (elementCount > _limits.maxTensorBytes ~/ bytesPerElement) {
      throw RangeError('Tensor byte length exceeds the configured limit.');
    }
    return elementCount * bytesPerElement;
  }

  @override
  bool operator ==(Object other) =>
      other is OrtShape && _listEquals(dimensions, other.dimensions);

  @override
  int get hashCode => Object.hashAll(dimensions);

  @override
  String toString() => dimensions.toString();
}

bool _listEquals(List<int> left, List<int> right) {
  if (identical(left, right)) return true;
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) return false;
  }
  return true;
}
