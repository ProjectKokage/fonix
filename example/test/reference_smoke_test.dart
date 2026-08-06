import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/inference_backend.dart';
import 'package:fonix_reference/src/reference_smoke.dart';

import 'fake_inference_backend.dart';

void main() {
  test('settles one exact run and double-closes one backend', () async {
    final FakeInferenceBackend backend = FakeInferenceBackend();
    final Future<ReferenceSmokeReceipt> running = runReferenceSmoke(backend);
    backend.startup.complete(fakeStartupReceipt());
    await Future<void>.delayed(Duration.zero);
    backend.runs.single.complete(fakeRunReceipt());

    final ReferenceSmokeReceipt receipt = await running;

    expect(receipt.outputValues, <int>[1, 4, 9, 16, 25, 36]);
    expect(receipt.activeProviders, <String>['cpu']);
    expect(receipt.toMap().keys, <String>[
      'schemaVersion',
      'status',
      'runtimeVersion',
      'runtimeSource',
      'runtimeOwner',
      'artifactFlavor',
      'platform',
      'architecture',
      'shimBuildId',
      'artifactSha256',
      'modelSha256',
      'outputValues',
      'activeProviders',
      'fullCpuAssignment',
      'doubleClose',
    ]);
    expect(receipt.toJsonString(), isNot(contains('\n')));
    expect(backend.closeCalls, 2);
    expect(backend.closeWorkCount, 1);
  });

  test('startup receipt owns and validates every native identity', () {
    final List<String> providers = <String>['cpu'];
    final InferenceStartupReceipt receipt = InferenceStartupReceipt(
      runtimeVersion: '1.27.1',
      runtimeSource: 'bundled',
      runtimeOwner: 'application',
      artifactFlavor: 'cpu',
      platform: 'android',
      architecture: 'arm64-v8a',
      shimBuildId: 'android-owner-application-source-bundled',
      artifactSha256:
          '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      modelSha256: referenceModelSha256,
      registeredProviders: providers,
    );
    providers.add('xnnpack');

    expect(receipt.registeredProviders, <String>['cpu']);
    expect(receipt.shimBuildId, 'android-owner-application-source-bundled');
    expect(
      receipt.artifactSha256,
      '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
    );
    expect(
      () => receipt.registeredProviders.add('xnnpack'),
      throwsUnsupportedError,
    );
  });

  test('startup receipt rejects paths, invalid digests, and duplicates', () {
    InferenceStartupReceipt create({
      String shimBuildId = 'fonix-test',
      String artifactSha256 =
          '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      Iterable<String> providers = const <String>['cpu'],
    }) => InferenceStartupReceipt(
      runtimeVersion: '1.27.1',
      runtimeSource: 'bundled',
      runtimeOwner: 'application',
      artifactFlavor: 'cpu',
      platform: 'android',
      architecture: 'arm64-v8a',
      shimBuildId: shimBuildId,
      artifactSha256: artifactSha256,
      modelSha256: referenceModelSha256,
      registeredProviders: providers,
    );

    expect(() => create(shimBuildId: '/private/shim'), throwsArgumentError);
    expect(() => create(artifactSha256: 'not-a-digest'), throwsArgumentError);
    expect(
      () => create(providers: const <String>['cpu', 'cpu']),
      throwsArgumentError,
    );
  });
}
