import 'dart:convert';

import 'resource_limits.dart';
import 'tensor_type.dart';

/// A concrete, dynamic, or symbolic model dimension.
final class OrtDimension {
  factory OrtDimension.fixed(int value) {
    if (value < 0 || value > 0x7fffffffffffffff) {
      throw RangeError.range(value, 0, 0x7fffffffffffffff, 'value');
    }
    return OrtDimension._(value, null);
  }

  factory OrtDimension.dynamic([String? symbol]) {
    if (symbol != null) {
      _metadataText(symbol, 'symbol', 256);
    }
    return OrtDimension._(null, symbol);
  }

  const OrtDimension._(this.value, this.symbol);

  final int? value;
  final String? symbol;

  bool get isDynamic => value == null;

  @override
  bool operator ==(Object other) =>
      other is OrtDimension && value == other.value && symbol == other.symbol;

  @override
  int get hashCode => Object.hash(value, symbol);

  @override
  String toString() => value?.toString() ?? symbol ?? '?';
}

/// Immutable, copied ONNX value type information.
final class OrtTypeInfo {
  factory OrtTypeInfo.tensor({
    required OrtTensorElementType elementType,
    List<OrtDimension> dimensions = const <OrtDimension>[],
    bool hasShape = true,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtTypeInfo(
    kind: OrtValueKind.tensor,
    tensorElementType: elementType,
    dimensions: dimensions,
    hasShape: hasShape,
    limits: limits,
  );

  factory OrtTypeInfo.sequence(
    OrtTypeInfo element, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtTypeInfo(
    kind: OrtValueKind.sequence,
    sequenceElement: element,
    limits: limits,
  );

  factory OrtTypeInfo.map({
    required OrtTensorElementType keyElementType,
    required OrtTypeInfo value,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtTypeInfo(
    kind: OrtValueKind.map,
    mapKeyType: keyElementType,
    mapValueType: value,
    limits: limits,
  );

  factory OrtTypeInfo.optional(
    OrtTypeInfo element, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtTypeInfo(
    kind: OrtValueKind.optional,
    optionalElement: element,
    limits: limits,
  );

  factory OrtTypeInfo({
    required OrtValueKind kind,
    OrtTensorElementType? tensorElementType,
    List<OrtDimension> dimensions = const <OrtDimension>[],
    bool? hasShape,
    OrtTypeInfo? sequenceElement,
    OrtTensorElementType? mapKeyType,
    OrtTypeInfo? mapValueType,
    OrtTypeInfo? optionalElement,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final int dimensionCount = dimensions.length;
    if (dimensionCount > limits.maxRank) {
      throw RangeError.range(
        dimensionCount,
        0,
        limits.maxRank,
        'dimensions.length',
      );
    }
    final List<OrtDimension> copiedDimensions = List<OrtDimension>.unmodifiable(
      List<OrtDimension>.generate(
        dimensionCount,
        (int index) => dimensions[index],
        growable: false,
      ),
    );
    final bool checkedHasShape =
        hasShape ??
        (kind == OrtValueKind.tensor || kind == OrtValueKind.sparseTensor);
    switch (kind) {
      case OrtValueKind.tensor:
      case OrtValueKind.sparseTensor:
        if (tensorElementType == null ||
            tensorElementType == OrtTensorElementType.undefined ||
            (!checkedHasShape && copiedDimensions.isNotEmpty) ||
            sequenceElement != null ||
            mapKeyType != null ||
            mapValueType != null ||
            optionalElement != null) {
          throw ArgumentError('Tensor type information is inconsistent.');
        }
      case OrtValueKind.sequence:
        if (sequenceElement == null ||
            tensorElementType != null ||
            checkedHasShape ||
            copiedDimensions.isNotEmpty ||
            mapKeyType != null ||
            mapValueType != null ||
            optionalElement != null) {
          throw ArgumentError('Sequence type information is inconsistent.');
        }
      case OrtValueKind.map:
        if ((mapKeyType != OrtTensorElementType.string &&
                mapKeyType != OrtTensorElementType.int64) ||
            mapValueType == null ||
            tensorElementType != null ||
            checkedHasShape ||
            copiedDimensions.isNotEmpty ||
            sequenceElement != null ||
            optionalElement != null) {
          throw ArgumentError('Map type information is inconsistent.');
        }
      case OrtValueKind.optional:
        if (optionalElement == null ||
            tensorElementType != null ||
            checkedHasShape ||
            copiedDimensions.isNotEmpty ||
            sequenceElement != null ||
            mapKeyType != null ||
            mapValueType != null) {
          throw ArgumentError('Optional type information is inconsistent.');
        }
      case OrtValueKind.unknown:
      case OrtValueKind.opaque:
        if (tensorElementType != null ||
            checkedHasShape ||
            copiedDimensions.isNotEmpty ||
            sequenceElement != null ||
            mapKeyType != null ||
            mapValueType != null ||
            optionalElement != null) {
          throw ArgumentError('Opaque type information is inconsistent.');
        }
    }
    final OrtTypeInfo result = OrtTypeInfo._(
      kind: kind,
      tensorElementType: tensorElementType,
      hasShape: checkedHasShape,
      dimensions: copiedDimensions,
      sequenceElement: sequenceElement,
      mapKeyType: mapKeyType,
      mapValueType: mapValueType,
      optionalElement: optionalElement,
    );
    _validateTypeInfoBounds(result, limits);
    return result;
  }

  const OrtTypeInfo._({
    required this.kind,
    required this.tensorElementType,
    required this.hasShape,
    required this.dimensions,
    required this.sequenceElement,
    required this.mapKeyType,
    required this.mapValueType,
    required this.optionalElement,
  });

  final OrtValueKind kind;
  final OrtTensorElementType? tensorElementType;

  /// Whether tensor metadata includes a shape.
  ///
  /// A shaped rank-zero tensor is a scalar. An unshaped tensor has unknown
  /// rank; both have an empty [dimensions] list and are distinguished here.
  final bool hasShape;
  final List<OrtDimension> dimensions;
  final OrtTypeInfo? sequenceElement;
  final OrtTensorElementType? mapKeyType;
  final OrtTypeInfo? mapValueType;
  final OrtTypeInfo? optionalElement;

  @override
  bool operator ==(Object other) =>
      other is OrtTypeInfo &&
      kind == other.kind &&
      tensorElementType == other.tensorElementType &&
      hasShape == other.hasShape &&
      _metadataListEquals(dimensions, other.dimensions) &&
      sequenceElement == other.sequenceElement &&
      mapKeyType == other.mapKeyType &&
      mapValueType == other.mapValueType &&
      optionalElement == other.optionalElement;

  @override
  int get hashCode => Object.hash(
    kind,
    tensorElementType,
    hasShape,
    Object.hashAll(dimensions),
    sequenceElement,
    mapKeyType,
    mapValueType,
    optionalElement,
  );
}

void _validateTypeInfoBounds(OrtTypeInfo root, OrtResourceLimits limits) {
  final List<(OrtTypeInfo, int)> pending = <(OrtTypeInfo, int)>[(root, 1)];
  var nodes = 0;
  while (pending.isNotEmpty) {
    final (OrtTypeInfo current, int depth) = pending.removeLast();
    nodes += 1;
    if (depth > limits.maxTypeDepth || nodes > limits.maxTypeNodes) {
      throw RangeError('Type information exceeds the configured limits.');
    }
    if (current.dimensions.length > limits.maxRank) {
      throw RangeError.range(
        current.dimensions.length,
        0,
        limits.maxRank,
        'dimensions.length',
      );
    }
    final OrtTypeInfo? child = switch (current.kind) {
      OrtValueKind.sequence => current.sequenceElement,
      OrtValueKind.map => current.mapValueType,
      OrtValueKind.optional => current.optionalElement,
      _ => null,
    };
    if (child != null) {
      pending.add((child, depth + 1));
    }
  }
}

/// A named model input or output whose metadata is copied into Dart.
final class OrtValueInfo {
  factory OrtValueInfo({required String name, required OrtTypeInfo type}) {
    _metadataText(name, 'name', 1024);
    return OrtValueInfo._(name, type);
  }

  const OrtValueInfo._(this.name, this.type);

  final String name;
  final OrtTypeInfo type;

  @override
  bool operator ==(Object other) =>
      other is OrtValueInfo && name == other.name && type == other.type;

  @override
  int get hashCode => Object.hash(name, type);
}

/// Immutable model metadata copied out of the native allocator.
final class OrtModelMetadata {
  factory OrtModelMetadata({
    String producerName = '',
    String graphName = '',
    String domain = '',
    String description = '',
    String graphDescription = '',
    int version = 0,
    Map<String, String> custom = const <String, String>{},
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _metadataText(producerName, 'producerName', 4096, allowEmpty: true);
    _metadataText(graphName, 'graphName', 4096, allowEmpty: true);
    _metadataText(domain, 'domain', 4096, allowEmpty: true);
    _metadataText(
      description,
      'description',
      64 * 1024,
      allowEmpty: true,
      allowControls: true,
    );
    _metadataText(
      graphDescription,
      'graphDescription',
      64 * 1024,
      allowEmpty: true,
      allowControls: true,
    );
    if (version < -0x8000000000000000 || version > 0x7fffffffffffffff) {
      throw RangeError.value(version, 'version', 'must fit in signed int64');
    }
    if (custom.length > limits.maxConfigEntries) {
      throw RangeError.range(
        custom.length,
        0,
        limits.maxConfigEntries,
        'custom.length',
      );
    }
    final copiedCustom = <String, String>{};
    for (final entry in custom.entries) {
      _metadataText(entry.key, 'custom metadata key', 1024);
      _metadataText(
        entry.value,
        'custom metadata value',
        64 * 1024,
        allowEmpty: true,
        allowControls: true,
      );
      copiedCustom[entry.key] = entry.value;
    }
    return OrtModelMetadata._(
      producerName: producerName,
      graphName: graphName,
      domain: domain,
      description: description,
      graphDescription: graphDescription,
      version: version,
      custom: Map<String, String>.unmodifiable(copiedCustom),
    );
  }

  const OrtModelMetadata._({
    required this.producerName,
    required this.graphName,
    required this.domain,
    required this.description,
    required this.graphDescription,
    required this.version,
    required this.custom,
  });

  final String producerName;
  final String graphName;
  final String domain;
  final String description;
  final String graphDescription;
  final int version;
  final Map<String, String> custom;

  @override
  bool operator ==(Object other) =>
      other is OrtModelMetadata &&
      producerName == other.producerName &&
      graphName == other.graphName &&
      domain == other.domain &&
      description == other.description &&
      graphDescription == other.graphDescription &&
      version == other.version &&
      _metadataMapEquals(custom, other.custom);

  @override
  int get hashCode => Object.hash(
    producerName,
    graphName,
    domain,
    description,
    graphDescription,
    version,
    _metadataMapHash(custom),
  );
}

void _metadataText(
  String value,
  String name,
  int maximumBytes, {
  bool allowEmpty = false,
  bool allowControls = false,
}) {
  if ((!allowEmpty && value.isEmpty) ||
      !_metadataHasWellFormedUtf16(value) ||
      value.length > maximumBytes ||
      utf8.encode(value).length > maximumBytes ||
      value.contains('\u0000') ||
      (!allowControls &&
          value.runes.any((int rune) => rune < 0x20 || rune == 0x7f))) {
    throw ArgumentError('$name is not bounded UTF-8 text.');
  }
}

bool _metadataHasWellFormedUtf16(String value) {
  for (var index = 0; index < value.length; index += 1) {
    final int unit = value.codeUnitAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      if (index + 1 >= value.length) return false;
      final int next = value.codeUnitAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

bool _metadataListEquals<T>(List<T> left, List<T> right) {
  if (identical(left, right)) return true;
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

bool _metadataMapEquals<K, V>(Map<K, V> left, Map<K, V> right) {
  if (identical(left, right)) return true;
  if (left.length != right.length) return false;
  for (final MapEntry<K, V> entry in left.entries) {
    if (!right.containsKey(entry.key) || right[entry.key] != entry.value) {
      return false;
    }
  }
  return true;
}

int _metadataMapHash(Map<String, String> value) {
  final List<String> keys = value.keys.toList(growable: false)..sort();
  return Object.hashAll(keys.map((String key) => Object.hash(key, value[key])));
}
