import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/android_smoke_channel.dart';
import 'package:fonix_reference/src/reference_smoke.dart';

import 'fake_inference_backend.dart';

const Set<String> _successReceiptKeys = <String>{
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
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('publishes the exact bounded Android success contract', () async {
    final TestDefaultBinaryMessenger messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    MethodCall? received;
    messenger.setMockMethodCallHandler(
      const MethodChannel(androidSmokeChannelName),
      (MethodCall call) async {
        received = call;
        return null;
      },
    );
    addTearDown(
      () => messenger.setMockMethodCallHandler(
        const MethodChannel(androidSmokeChannelName),
        null,
      ),
    );
    final ReferenceSmokeReceipt receipt = ReferenceSmokeReceipt.fromRun(
      startup: fakeStartupReceipt(
        platform: 'android',
        architecture: 'arm64-v8a',
        shimBuildId:
            'android-owner-application-source-bundled-artifact-onnxruntime-1.27.1-android-arm64-v8a-cpu',
        artifactSha256:
            '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      ),
      run: fakeRunReceipt(),
    );

    await const AndroidSmokeChannel().completePassed(receipt);

    expect(received?.method, androidSmokeCompleteMethod);
    final Map<Object?, Object?> arguments =
        (received?.arguments as Map<Object?, Object?>?)!;
    expect(arguments.keys.toSet(), <Object?>{
      'schemaVersion',
      'status',
      'receipt',
    });
    expect(arguments['schemaVersion'], 1);
    expect(arguments['status'], 0);
    final String encoded = arguments['receipt']! as String;
    expect(encoded, receipt.toJsonString());
    expect(encoded, isNot(contains('\n')));
    expect(encoded, isNot(contains('\r')));
    expect(utf8.encode(encoded).length, lessThanOrEqualTo(16 * 1024));
    final Map<String, Object?> decoded =
        (jsonDecode(encoded) as Map<String, Object?>);
    expect(decoded.keys.toSet(), _successReceiptKeys);
    expect(decoded['status'], 'passed');
    expect(decoded['platform'], 'android');
    expect(decoded['architecture'], 'arm64-v8a');
    expect(decoded['outputValues'], <Object?>[1, 4, 9, 16, 25, 36]);
    expect(decoded['activeProviders'], <Object?>['cpu']);
    expect(decoded['fullCpuAssignment'], isTrue);
  });

  test('publishes a closed failure receipt without private details', () async {
    final TestDefaultBinaryMessenger messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    MethodCall? received;
    messenger.setMockMethodCallHandler(
      const MethodChannel(androidSmokeChannelName),
      (MethodCall call) async {
        received = call;
        return null;
      },
    );
    addTearDown(
      () => messenger.setMockMethodCallHandler(
        const MethodChannel(androidSmokeChannelName),
        null,
      ),
    );

    await const AndroidSmokeChannel().completeFailed(
      StateError('/private/model/path'),
    );

    final Map<Object?, Object?> arguments =
        (received?.arguments as Map<Object?, Object?>?)!;
    expect(arguments.keys.toSet(), <Object?>{
      'schemaVersion',
      'status',
      'receipt',
    });
    expect(arguments['status'], 1);
    final String encoded = arguments['receipt']! as String;
    expect(encoded, isNot(contains('/private/model/path')));
    expect(jsonDecode(encoded), <String, Object?>{
      'schemaVersion': 1,
      'status': 'failed',
      'errorType': 'StateError',
    });
  });
}
