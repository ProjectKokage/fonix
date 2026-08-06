part of 'runtime.dart';

const int _maximumMetadataItems = 256;

/// Strict, fully copied session input/output metadata.
final class OrtSessionMetadata {
  OrtSessionMetadata._({
    required List<OrtValueInfo> inputs,
    required List<OrtValueInfo> outputs,
  }) : inputs = List<OrtValueInfo>.unmodifiable(inputs),
       outputs = List<OrtValueInfo>.unmodifiable(outputs);

  final List<OrtValueInfo> inputs;
  final List<OrtValueInfo> outputs;
}

/// Parses copied shim metadata for protocol-focused tests.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
OrtSessionMetadata parseOrtSessionMetadataForTesting(
  String source, {
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) => _parseOrtSessionMetadata(source, limits: limits);

/// Parses copied shim tensor information for protocol-focused tests.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
OrtTensorInfo parseOrtTensorInfoForTesting(
  String source, {
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) => _parseOrtTensorInfo(source, limits: limits);

/// Parses the isolated recursive Phase-3 type-metadata protocol.
///
/// This does not replace or reinterpret the flat Phase-2 session metadata
/// endpoint and is intentionally omitted from `package:fonix/fonix.dart` until
/// its native endpoint is stable.
OrtSessionMetadata parseOrtRecursiveTypeMetadataForTesting(
  String source, {
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) => _parseOrtRecursiveTypeMetadata(source, limits: limits);

/// Parses copied Phase-3 model metadata without retaining native state.
///
/// This testing adapter is intentionally omitted from the public package
/// library until the native endpoint is stable.
OrtModelMetadata parseOrtModelMetadataForTesting(
  String source, {
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) => _parseOrtModelMetadata(source, limits: limits);

OrtSessionMetadata _parseOrtSessionMetadata(
  String source, {
  required OrtResourceLimits limits,
}) {
  final Map<String, Object?> object = _protocolObject(
    source,
    'session metadata',
    limits.maxDiagnosticsBytes,
  );
  _protocolExactKeys(object, const <String>{
    'schemaVersion',
    'inputs',
    'outputs',
  });
  if (_protocolInt(object, 'schemaVersion') != 2) {
    throw const FormatException('Unsupported session metadata schema.');
  }
  final List<Object?> inputs = _protocolList(object, 'inputs');
  final List<Object?> outputs = _protocolList(object, 'outputs');
  if (inputs.length > _maximumMetadataItems ||
      outputs.length > _maximumMetadataItems) {
    throw const FormatException('Session metadata contains too many values.');
  }
  return OrtSessionMetadata._(
    inputs: _parseValueInfos(inputs, limits, 'inputs'),
    outputs: _parseValueInfos(outputs, limits, 'outputs'),
  );
}

OrtSessionMetadata _parseOrtRecursiveTypeMetadata(
  String source, {
  required OrtResourceLimits limits,
}) {
  final Map<String, Object?> object = _protocolObject(
    source,
    'recursive type metadata',
    limits.maxDiagnosticsBytes,
  );
  _protocolExactKeys(object, const <String>{
    'schemaVersion',
    'inputs',
    'outputs',
  });
  if (_protocolInt(object, 'schemaVersion') != 1) {
    throw const FormatException('Unsupported recursive type-metadata schema.');
  }
  final List<Object?> inputs = _protocolList(object, 'inputs');
  final List<Object?> outputs = _protocolList(object, 'outputs');
  if (inputs.length > _maximumMetadataItems ||
      outputs.length > _maximumMetadataItems) {
    throw const FormatException('Type metadata contains too many values.');
  }
  final _TypeParseBudget budget = _TypeParseBudget(limits);
  return OrtSessionMetadata._(
    inputs: _parseRecursiveValueInfos(inputs, limits, budget, 'inputs'),
    outputs: _parseRecursiveValueInfos(outputs, limits, budget, 'outputs'),
  );
}

OrtModelMetadata _parseOrtModelMetadata(
  String source, {
  required OrtResourceLimits limits,
}) {
  final Map<String, Object?> object = _protocolObject(
    source,
    'model metadata',
    limits.maxDiagnosticsBytes,
  );
  _protocolExactKeys(object, const <String>{
    'schemaVersion',
    'producerName',
    'graphName',
    'domain',
    'description',
    'graphDescription',
    'version',
    'customMetadata',
  });
  if (_protocolInt(object, 'schemaVersion') != 1) {
    throw const FormatException('Unsupported model-metadata schema.');
  }
  final Map<String, Object?> custom = _protocolMap(object, 'customMetadata');
  if (custom.length > limits.maxConfigEntries) {
    throw const FormatException('Model custom metadata has too many entries.');
  }
  final Map<String, String> parsedCustom = <String, String>{};
  for (final MapEntry<String, Object?> entry in custom.entries) {
    _protocolBoundedText(
      entry.key,
      'customMetadata key',
      1024,
      allowEmpty: false,
      allowControls: false,
    );
    final Object? value = entry.value;
    if (value is! String) {
      throw const FormatException(
        'Model custom metadata values must be strings.',
      );
    }
    _protocolBoundedText(
      value,
      'customMetadata value',
      64 * 1024,
      allowEmpty: true,
      allowControls: true,
    );
    parsedCustom[entry.key] = value;
  }
  final List<String> sortedCustomKeys = parsedCustom.keys.toList(
    growable: false,
  )..sort();
  final Map<String, String> sortedCustom = <String, String>{
    for (final String key in sortedCustomKeys) key: parsedCustom[key]!,
  };
  return OrtModelMetadata(
    producerName: _protocolModelText(object, 'producerName', 4096),
    graphName: _protocolModelText(object, 'graphName', 4096),
    domain: _protocolModelText(object, 'domain', 4096),
    description: _protocolModelText(
      object,
      'description',
      64 * 1024,
      allowControls: true,
    ),
    graphDescription: _protocolModelText(
      object,
      'graphDescription',
      64 * 1024,
      allowControls: true,
    ),
    version: _protocolSignedInt(object, 'version'),
    custom: sortedCustom,
    limits: limits,
  );
}

List<OrtValueInfo> _parseRecursiveValueInfos(
  List<Object?> values,
  OrtResourceLimits limits,
  _TypeParseBudget budget,
  String label,
) {
  final List<OrtValueInfo> output = <OrtValueInfo>[];
  final Set<String> names = <String>{};
  for (var index = 0; index < values.length; index += 1) {
    final Object? raw = values[index];
    if (raw is! Map<String, Object?>) {
      throw FormatException('$label[$index] must be an object.');
    }
    _protocolExactKeys(raw, const <String>{'name', 'type'});
    final String name = _protocolString(raw, 'name', 1024);
    if (!names.add(name)) {
      throw FormatException('$label contains a duplicate name.');
    }
    output.add(
      OrtValueInfo(
        name: name,
        type: _parseRecursiveTypeNode(
          _protocolMap(raw, 'type'),
          limits,
          budget,
          depth: 1,
        ),
      ),
    );
  }
  return output;
}

OrtTypeInfo _parseRecursiveTypeNode(
  Map<String, Object?> object,
  OrtResourceLimits limits,
  _TypeParseBudget budget, {
  required int depth,
}) {
  budget.consume(depth);
  final String kind = _protocolString(object, 'kind', 32);
  switch (kind) {
    case 'tensor':
      _protocolExactKeys(object, const <String>{
        'kind',
        'elementType',
        'hasShape',
        'dimensions',
        'symbolicDimensions',
      });
      final OrtTensorElementType elementType = _protocolElementType(
        object,
        'elementType',
      );
      final List<Object?> dimensions = _protocolList(object, 'dimensions');
      final List<Object?> symbolic = _protocolList(
        object,
        'symbolicDimensions',
      );
      final bool hasShape = _protocolBool(object, 'hasShape');
      final List<OrtDimension> parsedDimensions = _parseProtocolDimensions(
        dimensions,
        symbolic,
        hasShape: hasShape,
        limits: limits,
        label: 'Tensor',
      );
      return OrtTypeInfo.tensor(
        elementType: elementType,
        dimensions: parsedDimensions,
        hasShape: hasShape,
        limits: limits,
      );
    case 'sequence':
      _protocolExactKeys(object, const <String>{'kind', 'element'});
      return OrtTypeInfo.sequence(
        _parseRecursiveTypeNode(
          _protocolMap(object, 'element'),
          limits,
          budget,
          depth: depth + 1,
        ),
        limits: limits,
      );
    case 'map':
      _protocolExactKeys(object, const <String>{
        'kind',
        'keyElementType',
        'value',
      });
      final OrtTensorElementType keyType = _protocolElementType(
        object,
        'keyElementType',
      );
      if (keyType != OrtTensorElementType.string &&
          keyType != OrtTensorElementType.int64) {
        throw const FormatException(
          'Map keys must be string or signed int64 tensors.',
        );
      }
      return OrtTypeInfo.map(
        keyElementType: keyType,
        value: _parseRecursiveTypeNode(
          _protocolMap(object, 'value'),
          limits,
          budget,
          depth: depth + 1,
        ),
        limits: limits,
      );
    case 'optional':
      _protocolExactKeys(object, const <String>{'kind', 'element'});
      return OrtTypeInfo.optional(
        _parseRecursiveTypeNode(
          _protocolMap(object, 'element'),
          limits,
          budget,
          depth: depth + 1,
        ),
        limits: limits,
      );
    default:
      throw const FormatException(
        'Recursive metadata contains an unsupported value kind.',
      );
  }
}

OrtTensorElementType _protocolElementType(
  Map<String, Object?> object,
  String key,
) {
  final OrtTensorElementType result;
  try {
    result = OrtTensorElementType.fromNativeValue(_protocolInt(object, key));
  } on ArgumentError {
    throw FormatException('$key is an unknown tensor element type.');
  }
  if (result == OrtTensorElementType.undefined) {
    throw FormatException('$key cannot be undefined.');
  }
  return result;
}

final class _TypeParseBudget {
  _TypeParseBudget(this.limits);

  final OrtResourceLimits limits;
  int _nodes = 0;

  void consume(int depth) {
    if (depth > limits.maxTypeDepth) {
      throw const FormatException('Type metadata exceeds its depth limit.');
    }
    _nodes += 1;
    if (_nodes > limits.maxTypeNodes) {
      throw const FormatException('Type metadata exceeds its node limit.');
    }
  }
}

OrtTensorInfo _parseOrtTensorInfo(
  String source, {
  required OrtResourceLimits limits,
}) {
  final Map<String, Object?> object = _protocolObject(
    source,
    'tensor info',
    limits.maxDiagnosticsBytes,
  );
  _protocolExactKeys(object, const <String>{
    'schemaVersion',
    'kind',
    'elementType',
    'dimensions',
    'byteLength',
  });
  if (_protocolInt(object, 'schemaVersion') != 1 ||
      _protocolString(object, 'kind', 32) != 'tensor') {
    throw const FormatException('Unsupported tensor-info protocol.');
  }
  final OrtTensorElementType elementType;
  try {
    elementType = OrtTensorElementType.fromNativeValue(
      _protocolInt(object, 'elementType'),
    );
  } on ArgumentError {
    throw const FormatException('Tensor info has an unknown element type.');
  }
  final int? bytesPerElement = elementType.fixedStorageBytes;
  final bool isString = elementType == OrtTensorElementType.string;
  if (!isString &&
      (bytesPerElement == null || !elementType.supportsDenseCreation)) {
    throw FormatException(
      'Tensor type ${elementType.name} is not supported by the safe dense API.',
    );
  }
  final List<Object?> rawDimensions = _protocolList(object, 'dimensions');
  if (rawDimensions.length > limits.maxRank) {
    throw const FormatException('Tensor rank exceeds the configured limit.');
  }
  final List<int> dimensions = <int>[];
  for (var index = 0; index < rawDimensions.length; index += 1) {
    final Object? dimension = rawDimensions[index];
    if (dimension is! int || dimension < 0 || dimension > limits.maxDimension) {
      throw FormatException('dimensions[$index] is not a concrete dimension.');
    }
    dimensions.add(dimension);
  }
  final OrtShape shape;
  try {
    shape = OrtShape(dimensions, limits: limits);
  } on RangeError {
    throw const FormatException('Tensor shape exceeds the configured limits.');
  }
  final int byteLength = _protocolInt(object, 'byteLength');
  final int? requiredBytes;
  try {
    requiredBytes = isString ? null : shape.requiredBytes(bytesPerElement!);
  } on RangeError {
    throw const FormatException(
      'Tensor storage exceeds the configured limits.',
    );
  }
  if (byteLength < 0 ||
      byteLength > limits.maxTensorBytes ||
      (isString &&
          (shape.elementCount > _maximumStringTensorElements ||
              byteLength > _maximumStringTensorBytes)) ||
      (!isString && byteLength != requiredBytes)) {
    throw const FormatException(
      'Tensor byte length does not match its type and shape.',
    );
  }
  return OrtTensorInfo._(
    elementType: elementType,
    shape: shape,
    byteLength: byteLength,
  );
}

List<OrtValueInfo> _parseValueInfos(
  List<Object?> values,
  OrtResourceLimits limits,
  String label,
) {
  final List<OrtValueInfo> output = <OrtValueInfo>[];
  final Set<String> names = <String>{};
  for (var index = 0; index < values.length; index += 1) {
    final Object? raw = values[index];
    if (raw is! Map<String, Object?>) {
      throw FormatException('$label[$index] must be an object.');
    }
    _protocolExactKeys(raw, const <String>{
      'name',
      'kind',
      'elementType',
      'hasShape',
      'dimensions',
      'symbolicDimensions',
    });
    final String name = _protocolString(raw, 'name', 1024);
    if (!names.add(name)) {
      throw FormatException('$label contains a duplicate name.');
    }
    if (_protocolString(raw, 'kind', 32) != 'tensor') {
      throw FormatException('$label[$index] is not a tensor.');
    }
    final OrtTensorElementType elementType;
    try {
      elementType = OrtTensorElementType.fromNativeValue(
        _protocolInt(raw, 'elementType'),
      );
    } on ArgumentError {
      throw FormatException('$label[$index] has an unknown element type.');
    }
    if (elementType == OrtTensorElementType.undefined) {
      throw FormatException('$label[$index] has an undefined element type.');
    }
    final bool hasShape = _protocolBool(raw, 'hasShape');
    final List<Object?> dimensions = _protocolList(raw, 'dimensions');
    final List<Object?> symbolic = _protocolList(raw, 'symbolicDimensions');
    final List<OrtDimension> parsedDimensions = _parseProtocolDimensions(
      dimensions,
      symbolic,
      hasShape: hasShape,
      limits: limits,
      label: '$label[$index]',
    );
    output.add(
      OrtValueInfo(
        name: name,
        type: OrtTypeInfo(
          kind: OrtValueKind.tensor,
          tensorElementType: elementType,
          dimensions: parsedDimensions,
          hasShape: hasShape,
          limits: limits,
        ),
      ),
    );
  }
  return output;
}

List<OrtDimension> _parseProtocolDimensions(
  List<Object?> dimensions,
  List<Object?> symbolic, {
  required bool hasShape,
  required OrtResourceLimits limits,
  required String label,
}) {
  if (dimensions.length != symbolic.length ||
      dimensions.length > limits.maxRank ||
      (!hasShape && dimensions.isNotEmpty)) {
    throw FormatException('$label has inconsistent dimensions.');
  }
  final List<OrtDimension> parsed = <OrtDimension>[];
  for (var index = 0; index < dimensions.length; index += 1) {
    final Object? dimension = dimensions[index];
    final Object? symbol = symbolic[index];
    if (dimension is int &&
        dimension >= 0 &&
        dimension <= limits.maxDimension &&
        symbol == null) {
      parsed.add(OrtDimension.fixed(dimension));
    } else if (dimension == null && (symbol == null || symbol is String)) {
      if (symbol is String) {
        _protocolSafeText(symbol, '$label.symbolicDimensions[$index]', 256);
      }
      parsed.add(OrtDimension.dynamic(symbol as String?));
    } else {
      throw FormatException('$label has an invalid dimension at $index.');
    }
  }
  return parsed;
}

Map<String, Object?> _protocolObject(
  String source,
  String label,
  int maximumBytes,
) {
  if (!_hasWellFormedUtf16(source) ||
      source.length > maximumBytes ||
      utf8.encode(source).length > maximumBytes) {
    throw FormatException('$label exceeds the configured limit.');
  }
  validateStrictJsonInternal(source, label: label);
  final Object? decoded;
  try {
    decoded = jsonDecode(source);
  } on FormatException {
    throw FormatException('$label is not valid JSON.');
  }
  if (decoded is! Map<String, Object?>) {
    throw FormatException('$label must be an object.');
  }
  return decoded;
}

void _protocolExactKeys(Map<String, Object?> object, Set<String> keys) {
  if (object.length != keys.length || !object.keys.toSet().containsAll(keys)) {
    throw const FormatException(
      'Native protocol contains missing or unknown fields.',
    );
  }
}

int _protocolInt(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! int || value < 0 || value > 0x7fffffffffffffff) {
    throw FormatException('$key must be a bounded non-negative integer.');
  }
  return value;
}

bool _protocolBool(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! bool) {
    throw FormatException('$key must be a Boolean.');
  }
  return value;
}

int _protocolSignedInt(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! int ||
      value < -0x8000000000000000 ||
      value > 0x7fffffffffffffff) {
    throw FormatException('$key must be a bounded signed integer.');
  }
  return value;
}

String _protocolString(
  Map<String, Object?> object,
  String key,
  int maximumBytes,
) {
  final Object? value = object[key];
  if (value is! String) {
    throw FormatException('$key must be a string.');
  }
  _protocolSafeText(value, key, maximumBytes);
  return value;
}

String _protocolModelText(
  Map<String, Object?> object,
  String key,
  int maximumBytes, {
  bool allowControls = false,
}) {
  final Object? value = object[key];
  if (value is! String) {
    throw FormatException('$key must be a string.');
  }
  _protocolBoundedText(
    value,
    key,
    maximumBytes,
    allowEmpty: true,
    allowControls: allowControls,
  );
  return value;
}

void _protocolSafeText(String value, String key, int maximumBytes) {
  _protocolBoundedText(
    value,
    key,
    maximumBytes,
    allowEmpty: false,
    allowControls: false,
  );
}

void _protocolBoundedText(
  String value,
  String key,
  int maximumBytes, {
  required bool allowEmpty,
  required bool allowControls,
}) {
  if ((!allowEmpty && value.isEmpty) ||
      !_hasWellFormedUtf16(value) ||
      value.length > maximumBytes ||
      utf8.encode(value).length > maximumBytes ||
      value.contains('\u0000') ||
      (!allowControls &&
          value.runes.any((int rune) => rune < 0x20 || rune == 0x7f))) {
    throw FormatException('$key is not bounded safe UTF-8 text.');
  }
}

List<Object?> _protocolList(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! List<Object?>) {
    throw FormatException('$key must be a list.');
  }
  return value;
}

Map<String, Object?> _protocolMap(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! Map<String, Object?>) {
    throw FormatException('$key must be an object.');
  }
  return value;
}
