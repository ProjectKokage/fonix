part of 'runtime.dart';

/// Closed model input modes supported by Fonix.
enum OrtModelSourceKind { bytes, file }

/// Immutable model bytes or a validated absolute model path.
sealed class OrtModelSource {
  const OrtModelSource._(this.modelId);

  factory OrtModelSource.bytes(
    Uint8List bytes, {
    Map<String, Uint8List> externalData = const <String, Uint8List>{},
    String? modelId,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtBytesModelSource(
    bytes,
    externalData: externalData,
    modelId: modelId,
    limits: limits,
  );

  /// Copies an in-memory model and its complete, explicitly named external
  /// data set. No filesystem fallback is used during session creation.
  factory OrtModelSource.bytesWithExternalData(
    Uint8List bytes, {
    required Map<String, Uint8List> externalData,
    String? modelId,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtBytesModelSource(
    bytes,
    externalData: externalData,
    modelId: modelId,
    limits: limits,
  );

  factory OrtModelSource.file({
    required String absolutePath,
    String? allowedRoot,
    String? modelId,
  }) = OrtFileModelSource;

  final String? modelId;

  OrtModelSourceKind get kind;
}

final class OrtBytesModelSource extends OrtModelSource {
  factory OrtBytesModelSource(
    Uint8List bytes, {
    Map<String, Uint8List> externalData = const <String, Uint8List>{},
    String? modelId,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _validateModelId(modelId);
    if (bytes.isEmpty || bytes.length > limits.maxModelBytes) {
      throw RangeError.range(
        bytes.length,
        1,
        limits.maxModelBytes,
        'bytes.length',
      );
    }

    final Map<String, Uint8List> copiedExternalData = <String, Uint8List>{};
    final Set<String> seenNames = <String>{};
    var totalByteLength = bytes.length;
    var fileCount = 0;
    for (final MapEntry<String, Uint8List> entry in externalData.entries) {
      fileCount += 1;
      if (fileCount > _maxExternalDataFiles) {
        throw RangeError('External data exceeds the maximum number of files.');
      }
      _validateExternalDataName(entry.key);
      if (!seenNames.add(entry.key)) {
        throw ArgumentError('External data names must be unique.');
      }
      if (entry.value.length > limits.maxModelBytes - totalByteLength) {
        throw RangeError(
          'Model and external data exceed the configured byte limit.',
        );
      }
      totalByteLength += entry.value.length;
      copiedExternalData[entry.key] = Uint8List.fromList(entry.value);
    }

    return OrtBytesModelSource._(
      Uint8List.fromList(bytes),
      Map<String, Uint8List>.unmodifiable(copiedExternalData),
      totalByteLength,
      modelId,
    );
  }

  OrtBytesModelSource._(
    this._modelBytes,
    this._externalDataBytes,
    this.totalByteLength,
    super.modelId,
  ) : super._();

  final Uint8List _modelBytes;
  final Map<String, Uint8List> _externalDataBytes;

  @override
  OrtModelSourceKind get kind => OrtModelSourceKind.bytes;

  int get byteLength => _modelBytes.length;

  /// Total bytes copied from the model and every external-data entry.
  final int totalByteLength;

  bool get hasExternalData => _externalDataBytes.isNotEmpty;

  int get externalDataByteLength => totalByteLength - byteLength;

  /// Returns a copy so callers cannot mutate the bytes used for session load.
  Uint8List get bytes => Uint8List.fromList(_modelBytes);

  /// Returns ordered copies of the explicitly supplied external-data files.
  ///
  /// The map cannot be changed. Its byte lists are fresh copies, so changing a
  /// returned list cannot mutate this model source.
  Map<String, Uint8List> get externalData =>
      Map<String, Uint8List>.unmodifiable(<String, Uint8List>{
        for (final MapEntry<String, Uint8List> entry
            in _externalDataBytes.entries)
          entry.key: Uint8List.fromList(entry.value),
      });
}

final class OrtFileModelSource extends OrtModelSource {
  factory OrtFileModelSource({
    required String absolutePath,
    String? allowedRoot,
    String? modelId,
  }) {
    _validateModelId(modelId);
    _validateModelPath(absolutePath, 'absolutePath');
    if (!p.isAbsolute(absolutePath)) {
      throw ArgumentError('absolutePath must be absolute.');
    }
    final String normalizedPath = p.normalize(absolutePath);
    var normalizedRoot = p.dirname(normalizedPath);
    if (allowedRoot != null) {
      _validateModelPath(allowedRoot, 'allowedRoot');
      if (!p.isAbsolute(allowedRoot)) {
        throw ArgumentError('allowedRoot must be absolute.');
      }
      normalizedRoot = p.normalize(allowedRoot);
      if (normalizedPath != normalizedRoot &&
          !p.isWithin(normalizedRoot, normalizedPath)) {
        throw ArgumentError(
          'absolutePath must be lexically contained by allowedRoot.',
        );
      }
    }
    return OrtFileModelSource._(normalizedPath, normalizedRoot, modelId);
  }

  const OrtFileModelSource._(this.absolutePath, this.allowedRoot, super.modelId)
    : super._();

  @override
  OrtModelSourceKind get kind => OrtModelSourceKind.file;

  final String absolutePath;
  final String allowedRoot;
}

void _validateModelId(String? value) {
  if (value == null) return;
  if (value.isEmpty ||
      !_hasWellFormedUtf16(value) ||
      value.length > 256 ||
      utf8.encode(value).length > 256 ||
      value.runes.any((int rune) => rune < 0x20 || rune == 0x7f)) {
    throw ArgumentError('modelId must be bounded safe UTF-8 text.');
  }
}

void _validateModelPath(String value, String name) {
  if (value.isEmpty ||
      !_hasWellFormedUtf16(value) ||
      value.length > 4096 ||
      utf8.encode(value).length > 4096 ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r')) {
    throw ArgumentError('$name is not a bounded native path.');
  }
}

const int _maxExternalDataFiles = 256;
const int _maxExternalDataNameBytes = 1024;

void _validateExternalDataName(String value) {
  if (value.isEmpty ||
      value.length > _maxExternalDataNameBytes ||
      !_hasWellFormedUtf16(value) ||
      value.contains('\u0000') ||
      value.contains('\\') ||
      value.contains(':') ||
      value.startsWith('/') ||
      value.runes.any((int rune) => rune < 0x20 || rune == 0x7f)) {
    throw ArgumentError(
      'External data name must be a safe relative UTF-8 path.',
    );
  }
  if (utf8.encode(value).length > _maxExternalDataNameBytes) {
    throw ArgumentError(
      'External data name must be a safe relative UTF-8 path.',
    );
  }
  final List<String> components = value.split('/');
  if (components.any(
    (String component) =>
        component.isEmpty || component == '.' || component == '..',
  )) {
    throw ArgumentError(
      'External data name must be a safe relative UTF-8 path.',
    );
  }
}
