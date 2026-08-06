import 'dart:convert';

import 'package:path/path.dart' as p;

/// How the project-owned shim locates the process's single ONNX Runtime.
enum OrtRuntimeSourceKind { linked, bundled, process, file }

/// A closed, validated source from which the shim may resolve ONNX Runtime.
sealed class OrtRuntimeSource {
  const OrtRuntimeSource._();

  /// Resolves the ORT symbol linked into the application image.
  const factory OrtRuntimeSource.linked() = OrtLinkedRuntimeSource;

  /// Resolves the package-pinned runtime adjacent to the shim.
  const factory OrtRuntimeSource.bundled() = OrtBundledRuntimeSource;

  /// Resolves an application-owned runtime from constrained library names.
  factory OrtRuntimeSource.process({List<String> preferredLibraryNames}) =
      OrtProcessRuntimeSource;

  /// Opens a trusted desktop runtime from an absolute path.
  factory OrtRuntimeSource.file({
    required String absolutePath,
    String? allowedRoot,
  }) = OrtFileRuntimeSource;

  OrtRuntimeSourceKind get kind;

  /// The explicit runtime path, when [kind] is [OrtRuntimeSourceKind.file].
  String? get libraryPath => null;

  /// The lexical allow-root for an explicit file source.
  ///
  /// The native loader additionally canonicalizes both paths and rejects
  /// symlink escapes immediately before loading.
  String? get allowedRoot => null;

  /// Constrained basenames considered by process mode.
  List<String> get preferredLibraryNames => const <String>[];
}

final class OrtLinkedRuntimeSource extends OrtRuntimeSource {
  const OrtLinkedRuntimeSource() : super._();

  @override
  OrtRuntimeSourceKind get kind => OrtRuntimeSourceKind.linked;
}

final class OrtBundledRuntimeSource extends OrtRuntimeSource {
  const OrtBundledRuntimeSource() : super._();

  @override
  OrtRuntimeSourceKind get kind => OrtRuntimeSourceKind.bundled;
}

final class OrtProcessRuntimeSource extends OrtRuntimeSource {
  factory OrtProcessRuntimeSource({
    List<String> preferredLibraryNames = const <String>[],
  }) {
    if (preferredLibraryNames.length > 8) {
      throw RangeError.range(
        preferredLibraryNames.length,
        0,
        8,
        'preferredLibraryNames.length',
      );
    }
    final seen = <String>{};
    final names = <String>[];
    for (final name in preferredLibraryNames) {
      if (name.isEmpty ||
          name.length > 128 ||
          utf8.encode(name).length > 128 ||
          !_libraryName.hasMatch(name)) {
        throw ArgumentError.value(
          name,
          'preferredLibraryNames',
          'must contain only constrained library basenames',
        );
      }
      if (!seen.add(name)) {
        throw ArgumentError.value(
          name,
          'preferredLibraryNames',
          'contains a duplicate library name',
        );
      }
      names.add(name);
    }
    return OrtProcessRuntimeSource._(List<String>.unmodifiable(names));
  }

  const OrtProcessRuntimeSource._(this._preferredLibraryNames) : super._();

  static final RegExp _libraryName = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$');

  final List<String> _preferredLibraryNames;

  @override
  OrtRuntimeSourceKind get kind => OrtRuntimeSourceKind.process;

  @override
  List<String> get preferredLibraryNames => _preferredLibraryNames;
}

final class OrtFileRuntimeSource extends OrtRuntimeSource {
  factory OrtFileRuntimeSource({
    required String absolutePath,
    String? allowedRoot,
  }) {
    _validatePath(absolutePath, 'absolutePath');
    if (!p.isAbsolute(absolutePath)) {
      throw ArgumentError('absolutePath must be absolute.');
    }
    final normalizedPath = p.normalize(absolutePath);
    String? normalizedRoot;
    if (allowedRoot != null) {
      _validatePath(allowedRoot, 'allowedRoot');
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
    return OrtFileRuntimeSource._(normalizedPath, normalizedRoot);
  }

  const OrtFileRuntimeSource._(this._libraryPath, this._allowedRoot)
    : super._();

  final String _libraryPath;
  final String? _allowedRoot;

  @override
  OrtRuntimeSourceKind get kind => OrtRuntimeSourceKind.file;

  @override
  String get libraryPath => _libraryPath;

  @override
  String? get allowedRoot => _allowedRoot;

  static void _validatePath(String value, String name) {
    if (value.isEmpty ||
        value.length > 4096 ||
        utf8.encode(value).length > 4096 ||
        value.contains('\u0000') ||
        value.contains('\n') ||
        value.contains('\r')) {
      throw ArgumentError('$name is not a bounded native path.');
    }
  }
}
