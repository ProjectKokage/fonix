import 'dart:convert';
import 'dart:io';

import 'package:fonix/src/build/native_versions_lock.dart';
import 'package:path/path.dart' as path;
import 'package:test/test.dart';

const _lockPath = 'native/versions.lock.yaml';
const _schemaPath = 'native/versions.lock.schema.json';

void main() {
  group('active native version lock', () {
    test('passes development validation with exact pinned inputs', () {
      final lock = NativeVersionsLock.parse(File(_lockPath).readAsStringSync());

      expect(lock.schema, 2);
      expect(lock.releaseState, 'unreleased-preview');
      expect(lock.shim.abi, 1);
      expect(lock.shim.requiredOrtApi, 27);
      expect(lock.onnxRuntime.compatibilityApi, 27);
      expect(lock.onnxRuntime.compatibilityHeader.sizeBytes, 392401);
      expect(lock.onnxRuntime.epHeader.sizeBytes, 149810);
      expect(lock.onnxRuntime.license.sizeBytes, 1073);
      expect(lock.artifacts, hasLength(9));
      expect(
        lock.artifacts.map(_artifactIdentity).toSet(),
        lock.releaseTargets.map((target) => target.identity).toSet(),
      );
      expect(
        lock.artifacts.where((artifact) => artifact.containers.isNotEmpty),
        hasLength(5),
      );
      expect(
        lock.artifacts.expand((artifact) => artifact.notices),
        hasLength(18),
      );
      expect(lock.releaseTargets, hasLength(9));

      final iosSimulatorArtifacts = lock.artifacts
          .where(
            (artifact) =>
                artifact.target.operatingSystem == 'ios' &&
                artifact.target.variant == 'simulator',
          )
          .toList(growable: false);
      expect(iosSimulatorArtifacts, hasLength(2));
      expect(
        iosSimulatorArtifacts
            .map((artifact) => artifact.target.architecture)
            .toSet(),
        <String>{'arm64', 'x86_64'},
      );
      expect(
        iosSimulatorArtifacts
            .map((artifact) => artifact.expectedFiles.first.sha256)
            .toSet(),
        <String>{
          'c89260dbff67795fc3a96e40e389a38e52e29f0e2f5660ae202f670b9c79af0f',
        },
      );

      lock.verifyVendoredInputs(Directory.current);
    });

    test('schema is valid JSON Schema metadata', () {
      final schema = _object(jsonDecode(File(_schemaPath).readAsStringSync()));
      final definitions = _object(schema[r'$defs']);

      expect(
        schema[r'$schema'],
        'https://json-schema.org/draft/2020-12/schema',
      );
      expect(schema['additionalProperties'], isFalse);
      expect(
        definitions,
        containsPair('artifactContainer', isA<Map<String, Object?>>()),
      );
      expect(definitions, contains('artifactNotice'));

      final releaseTargetsSchema = _object(
        _object(schema['properties'])['release_targets'],
      );
      expect(releaseTargetsSchema['uniqueItems'], isTrue);
      final schemaTargetIdentities =
          _list(_object(definitions['releaseTarget'])['enum']).map((value) {
            final target = _object(value);
            return '${target['os']}/${target['architecture']}/'
                '${target['variant']}/${target['flavor']}';
          }).toSet();
      final lock = NativeVersionsLock.parse(File(_lockPath).readAsStringSync());
      expect(
        schemaTargetIdentities,
        lock.releaseTargets.map((target) => target.identity).toSet(),
      );
      expect(_list(_object(definitions['target'])['allOf']), hasLength(5));
    });

    test('fails release validation while the Tier-1 matrix is incomplete', () {
      final value = _activeValue();
      value['release_state'] = 'release';
      _object(value['shim'])['source_revision'] = _repeat('a', 40);
      _makeProviderNamesConcrete(value);
      _list(value['artifacts']).removeLast();

      expect(
        () => _parse(value, mode: NativeVersionsLockValidationMode.release),
        _formatErrorContaining('does not cover release targets'),
      );
    });
  });

  group('development and release modes', () {
    test('development permits no runtime artifacts', () {
      final value = _activeValue()..['artifacts'] = <Object?>[];

      final lock = _parse(value);

      expect(lock.artifacts, isEmpty);
    });

    test('release requires an explicit release state', () {
      final value = _completeReleaseValue()
        ..['release_state'] = 'unreleased-preview';

      expect(
        () => _parse(value, mode: NativeVersionsLockValidationMode.release),
        _formatErrorContaining(r'$.release_state'),
      );
    });

    test('release requires a source revision', () {
      final value = _completeReleaseValue();
      _object(value['shim'])['source_revision'] = null;

      expect(
        () => _parse(value, mode: NativeVersionsLockValidationMode.release),
        _formatErrorContaining(r'$.shim.source_revision'),
      );
    });

    test('complete pinned Tier-1 matrix passes release validation', () {
      final lock = _parse(
        _completeReleaseValue(),
        mode: NativeVersionsLockValidationMode.release,
      );

      expect(lock.artifacts, hasLength(9));
      expect(lock.releaseState, 'release');
    });
  });

  group('closed schema', () {
    test('rejects literal and escape-equivalent duplicate JSON keys', () {
      final source = File(_lockPath).readAsStringSync();
      final literalDuplicate = source.replaceFirst(
        '"schema": 2,',
        '"schema": 2, "schema": 2,',
      );
      final escapedDuplicate = source.replaceFirst(
        '"schema": 2,',
        '"schema": 2, "sch\\u0065ma": 2,',
      );

      expect(
        () => NativeVersionsLock.parse(literalDuplicate),
        _formatErrorContaining('duplicate JSON object key'),
      );
      expect(
        () => NativeVersionsLock.parse(escapedDuplicate),
        _formatErrorContaining('duplicate JSON object key'),
      );
    });

    test('rejects unknown top-level fields', () {
      final value = _activeValue()..['unexpected'] = true;

      expect(() => _parse(value), _formatErrorContaining('unknown fields'));
    });

    test('rejects unknown nested fields', () {
      final value = _activeValue();
      final floor = _object(
        _object(value['onnxruntime'])['compatibility_floor'],
      );
      floor['moving_latest'] = true;

      expect(
        () => _parse(value),
        _formatErrorContaining(r'$.onnxruntime.compatibility_floor'),
      );
    });

    test('rejects missing required fields', () {
      final value = _activeValue()..remove('snapshot_date');

      expect(
        () => _parse(value),
        _formatErrorContaining('missing required fields'),
      );
    });

    test('rejects a mutated release target matrix', () {
      final value = _activeValue();
      _list(value['release_targets']).removeLast();

      expect(() => _parse(value), _formatErrorContaining('Tier-1 CPU matrix'));
    });
  });

  group('supply-chain values', () {
    test('rejects placeholder markers', () {
      final value = _activeValue();
      final header = _header(value);
      header['source_ref'] = 'REPLACE_WITH_TAG';

      expect(() => _parse(value), _formatErrorContaining('placeholder marker'));
    });

    test('rejects escaped control characters in scalar text', () {
      final value = _activeValue();
      _header(value)['source_ref'] = 'v1.27.1\u0000suffix';

      expect(() => _parse(value), _formatErrorContaining('control characters'));
    });

    test('rejects an unpaired UTF-16 surrogate', () {
      final source = jsonEncode(
        _activeValue(),
      ).replaceFirst(r'"source_ref":"v1.27.1"', r'"source_ref":"\ud800"');

      expect(
        () => NativeVersionsLock.parse(source),
        _formatErrorContaining('well-formed Unicode'),
      );
    });

    test('bounds scalar strings by UTF-8 bytes', () {
      final value = _activeValue();
      _firstArtifact(value)['id'] = _repeat('a', 128);
      _object(_firstArtifact(value)['build'])['toolchain'] = _repeat('界', 86);

      expect(() => _parse(value), _formatErrorContaining('256 UTF-8 bytes'));
    });

    test('rejects malformed SHA-256', () {
      final value = _activeValue();
      _header(value)['sha256'] = 'abc';

      expect(() => _parse(value), _formatErrorContaining('SHA-256'));
    });

    test('rejects non-HTTPS and query-bearing URLs', () {
      final nonHttps = _activeValue();
      _artifactSource(nonHttps)['url'] = 'http://downloads.invalid/runtime.tgz';
      final query = _activeValue();
      _artifactSource(query)['url'] =
          'https://downloads.invalid/runtime.tgz?token=secret';

      expect(() => _parse(nonHttps), _formatErrorContaining('absolute HTTPS'));
      expect(() => _parse(query), _formatErrorContaining('absolute HTTPS'));
    });

    test('rejects invalid target and runtime mode combinations', () {
      final target = _activeValue();
      _artifactTarget(target)['architecture'] = 'x64';
      final variant = _activeValue();
      _artifactTarget(variant)['variant'] = 'default';
      final mode = _activeValue();
      _firstArtifact(mode)['runtime_mode'] = 'external';

      expect(() => _parse(target), _formatErrorContaining('architecture'));
      expect(() => _parse(variant), _formatErrorContaining('variant'));
      expect(() => _parse(mode), _formatErrorContaining('runtime_mode'));
    });

    test('rejects path traversal and duplicate expected files', () {
      final traversal = _activeValue();
      _firstExpectedFile(traversal)['path'] = '../libonnxruntime.dylib';
      final duplicate = _activeValue();
      final files = _list(_firstArtifact(duplicate)['expected_files']);
      files.add(_deepCopy(files.first));

      expect(
        () => _parse(traversal),
        _formatErrorContaining('canonical relative POSIX path'),
      );
      expect(
        () => _parse(duplicate),
        _formatErrorContaining('duplicates expected path'),
      );
    });

    test('rejects a symlink to an unlisted regular file', () {
      final value = _activeValue();
      final symlink = _object(
        _list(_macosArtifact(value)['expected_symlinks']).single,
      );
      symlink['target'] = 'unlisted.dylib';

      expect(
        () => _parse(value),
        _formatErrorContaining('expected regular file'),
      );
    });

    test('rejects invalid nested container and notice metadata', () {
      final traversal = _activeValue();
      _object(_list(_firstArtifact(traversal)['containers']).single)['path'] =
          '../payload.zip';
      final depth = _activeValue();
      _object(
        _list(_firstArtifact(depth)['notices']).first,
      )['container_depth'] = 2;
      final overlap = _activeValue();
      final overlapArtifact = _firstArtifact(overlap);
      _object(_list(overlapArtifact['notices']).first)['path'] = _object(
        _list(overlapArtifact['containers']).first,
      )['path'];

      expect(
        () => _parse(traversal),
        _formatErrorContaining('canonical relative POSIX path'),
      );
      expect(
        () => _parse(depth),
        _formatErrorContaining('integer from 0 through 1'),
      );
      expect(
        () => _parse(overlap),
        _formatErrorContaining('overlaps another selected member'),
      );
    });

    test('rejects duplicate staged paths across payload and notices', () {
      final value = _activeValue();
      final artifact = _firstArtifact(value);
      final stagedPath = _firstExpectedFile(value)['staged_path'];
      _object(_list(artifact['notices']).first)['staged_path'] = stagedPath;

      expect(() => _parse(value), _formatErrorContaining('overlaps another'));
    });

    test('rejects source larger than the bounded input limit', () {
      final oversized = _repeat('x', nativeVersionsLockMaxBytes + 1);

      expect(
        () => NativeVersionsLock.parse(oversized),
        _formatErrorContaining('size limit'),
      );
    });

    test('detects vendored input byte changes', () {
      final temporaryRoot = Directory.systemTemp.createTempSync(
        'fonix-lock-test-',
      );
      addTearDown(() => temporaryRoot.deleteSync(recursive: true));
      final lock = NativeVersionsLock.parse(File(_lockPath).readAsStringSync());
      final inputs = [
        lock.onnxRuntime.compatibilityHeader,
        lock.onnxRuntime.epHeader,
        lock.onnxRuntime.license,
      ];
      for (final input in inputs) {
        final destination = File(
          path.join(temporaryRoot.path, input.relativePath),
        );
        destination.parent.createSync(recursive: true);
        File(input.relativePath).copySync(destination.path);
      }
      final header = File(
        path.join(
          temporaryRoot.path,
          lock.onnxRuntime.compatibilityHeader.relativePath,
        ),
      );
      final bytes = header.readAsBytesSync();
      bytes[0] ^= 0x01;
      header.writeAsBytesSync(bytes);

      expect(
        () => lock.verifyVendoredInputs(temporaryRoot),
        throwsA(
          isA<StateError>().having(
            (error) => error.message,
            'message',
            contains('SHA-256'),
          ),
        ),
      );
    });
  });
}

NativeVersionsLock _parse(
  Map<String, Object?> value, {
  NativeVersionsLockValidationMode mode =
      NativeVersionsLockValidationMode.development,
}) => NativeVersionsLock.parse(jsonEncode(value), mode: mode);

Map<String, Object?> _activeValue() =>
    _object(jsonDecode(File(_lockPath).readAsStringSync()));

Map<String, Object?> _completeReleaseValue() {
  final value = _activeValue();
  value['release_state'] = 'release';
  _object(value['shim'])['source_revision'] = _repeat('a', 40);
  _makeProviderNamesConcrete(value);
  return value;
}

void _makeProviderNamesConcrete(Map<String, Object?> value) {
  for (final artifactValue in _list(value['artifacts'])) {
    for (final providerValue in _list(_object(artifactValue)['providers'])) {
      final provider = _object(providerValue);
      provider['reported_name'] ??=
          '${provider['wrapper_id']}ExecutionProvider';
    }
  }
}

String _artifactIdentity(NativeArtifactLock artifact) =>
    '${artifact.target.operatingSystem}/${artifact.target.architecture}/'
    '${artifact.target.variant}/${artifact.flavor}';

Map<String, Object?> _header(Map<String, Object?> value) => _object(
  _object(_object(value['onnxruntime'])['compatibility_floor'])['header'],
);

Map<String, Object?> _firstArtifact(Map<String, Object?> value) =>
    _object(_list(value['artifacts']).first);

Map<String, Object?> _macosArtifact(Map<String, Object?> value) => _object(
  _list(value['artifacts']).singleWhere(
    (artifact) => _object(_object(artifact)['target'])['os'] == 'macos',
  ),
);

Map<String, Object?> _artifactSource(Map<String, Object?> value) =>
    _object(_firstArtifact(value)['source']);

Map<String, Object?> _artifactTarget(Map<String, Object?> value) =>
    _object(_firstArtifact(value)['target']);

Map<String, Object?> _firstExpectedFile(Map<String, Object?> value) =>
    _object(_list(_firstArtifact(value)['expected_files']).first);

Map<String, Object?> _object(Object? value) => value! as Map<String, Object?>;

List<Object?> _list(Object? value) => value! as List<Object?>;

Object? _deepCopy(Object? value) => jsonDecode(jsonEncode(value));

String _repeat(String value, int count) => List.filled(count, value).join();

Matcher _formatErrorContaining(String text) => throwsA(
  isA<FormatException>().having(
    (error) => error.message,
    'message',
    contains(text),
  ),
);
