import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/runtime.dart';
import 'package:fonix/src/runtime_info.dart';
import 'package:test/test.dart';

void main() {
  group('strict native JSON protocols', () {
    test('diagnostics rejects root and nested duplicate keys', () {
      final String valid = jsonEncode(_validDiagnostics());
      expect(
        () => OrtDiagnostics.fromJsonString(
          valid.replaceFirst(
            '"schemaVersion":1',
            '"schemaVersion":1,"schemaVersion":1',
          ),
        ),
        throwsFormatException,
      );
      expect(
        () => OrtDiagnostics.fromJsonString(
          valid.replaceFirst(
            '"options":{}',
            '"options":{"device_id":"0","device_id":"1"}',
          ),
        ),
        throwsFormatException,
      );
    });

    test('session and model metadata reject ambiguous nested objects', () {
      expect(
        () => parseOrtSessionMetadataForTesting(
          '{"schemaVersion":2,"inputs":[],"\\u0069nputs":[],"outputs":[]}',
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtModelMetadataForTesting(
          '{'
          '"schemaVersion":1,'
          '"producerName":"",'
          '"graphName":"",'
          '"domain":"",'
          '"description":"",'
          '"graphDescription":"",'
          '"version":0,'
          '"customMetadata":{"key":"first","key":"second"}'
          '}',
        ),
        throwsFormatException,
      );
    });

    test('build manifest rejects escape-equivalent and deeply nested keys', () {
      final String valid = jsonEncode(_validBuildInfo());
      expect(
        () => parseOrtNativeBuildInfo(
          valid.replaceFirst(
            '"nativeIdentity":"fonix_shim"',
            '"nativeIdentity":"fonix_shim",'
                '"native\\u0049dentity":"fonix_shim"',
          ),
        ),
        throwsFormatException,
      );

      final String nested =
          '${List<String>.filled(33, '[').join()}0'
          '${List<String>.filled(33, ']').join()}';
      expect(() => parseOrtNativeBuildInfo(nested), throwsFormatException);
    });

    test('valid protocols retain their existing typed behavior', () {
      final OrtDiagnostics diagnostics = OrtDiagnostics.fromJsonString(
        jsonEncode(_validDiagnostics()),
      );
      expect(diagnostics.runtimeVersion, '1.27.1');
      expect(diagnostics.providers.single.wrapperId, 'cpu');

      final OrtNativeBuildInfo buildInfo = parseOrtNativeBuildInfo(
        jsonEncode(_validBuildInfo()),
      );
      expect(buildInfo.schemaVersion, 3);
      expect(buildInfo.allowedRuntimeSources, <OrtRuntimeSourceKind>[
        OrtRuntimeSourceKind.process,
        OrtRuntimeSourceKind.file,
      ]);
    });
  });
}

Map<String, Object?> _validBuildInfo() => <String, Object?>{
  'schemaVersion': 3,
  'nativeIdentity': 'fonix_shim',
  'shimAbiVersion': 1,
  'requiredOrtApiVersion': 27,
  'runtimeProfile': 'external',
  'androidRuntimeOwner': null,
  'allowedRuntimeSources': <String>['process', 'file'],
  'buildId': 'fonix-test-external',
  'artifact': null,
};

Map<String, Object?> _validDiagnostics() => <String, Object?>{
  'schemaVersion': 1,
  'dartPackageVersion': '0.1.0-dev.1',
  'shimAbiVersion': 1,
  'shimBuildId': 'fonix-test-external',
  'requiredOrtApiVersion': 27,
  'negotiatedOrtApiVersion': 27,
  'runtimeVersion': '1.27.1',
  'runtimeOwner': 'application',
  'runtimeMode': 'file',
  'runtimeIdentity': 'sha256:aaaaaaaa',
  'platform': 'macos',
  'architecture': 'arm64',
  'artifactFlavor': 'cpu',
  'artifactSha256': List<String>.filled(64, 'a').join(),
  'modelId': null,
  'session': null,
  'providers': <Object?>[
    <String, Object?>{
      'wrapperId': 'cpu',
      'registrationMechanism': 'implicit',
      'registrationName': null,
      'reportedName': 'CPUExecutionProvider',
      'compiled': null,
      'discoverable': true,
      'registered': true,
      'active': true,
      'qualified': null,
      'assignmentEvidence': 'ort-run-profile-v1:1',
      'fallbackReason': null,
      'options': <String, String>{},
    },
  ],
};
