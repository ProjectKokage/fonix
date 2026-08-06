import 'dart:convert';

import 'package:fonix/src/runtime.dart' show coreMlCacheIdentityForTesting;
import 'package:test/test.dart';

void main() {
  test('Core ML cache identity drifts with structured build policy', () {
    final Map<String, Object?> baseBuild = <String, Object?>{
      'schemaVersion': 3,
      'abiVersion': 1,
      'ortApiCompatibilityFloor': 27,
      'runtimeProfile': 'external',
      'buildId': 'macos-coreml-a',
      'androidRuntimeOwner': null,
      'allowedRuntimeSources': <String>['process', 'file'],
      'nativeIdentity': 'fonix-shim',
      'artifact': null,
    };
    String identity(Map<String, Object?> buildInfo) =>
        coreMlCacheIdentityForTesting(
          buildInfo: buildInfo,
          runtimeVersion: '1.27.1',
          operatingSystem: 'macos',
          architecture: 'arm64',
          applicationSchema: 'app-v1',
          modelSha256: 'a' * 64,
          providerId: 'coreml',
          providerOptions: const <String, String>{
            'MLComputeUnits': 'CPUOnly',
            'ModelFormat': 'NeuralNetwork',
          },
        );
    Map<String, Object?> changed(String field, Object? value) {
      final Map<String, Object?> copy =
          (jsonDecode(jsonEncode(baseBuild)) as Map<Object?, Object?>).map(
            (Object? key, Object? value) =>
                MapEntry<String, Object?>(key! as String, value),
          );
      copy[field] = value;
      return copy;
    }

    final String base = identity(baseBuild);
    expect(identity(changed('buildId', 'macos-coreml-b')), isNot(base));
    expect(identity(changed('androidRuntimeOwner', 'sherpa')), isNot(base));
    expect(
      identity(changed('allowedRuntimeSources', <String>['process'])),
      isNot(base),
    );
    expect(
      coreMlCacheIdentityForTesting(
        buildInfo: baseBuild,
        runtimeVersion: '1.27.1',
        operatingSystem: 'macos',
        architecture: 'arm64',
        applicationSchema: 'app-v1',
        modelSha256: 'a' * 64,
        providerId: 'coreml',
        providerOptions: const <String, String>{
          'ModelFormat': 'NeuralNetwork',
          'MLComputeUnits': 'CPUOnly',
        },
      ),
      base,
      reason: 'provider option order is normalized before hashing',
    );
  });
}
