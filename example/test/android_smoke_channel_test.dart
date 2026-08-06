import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/android_smoke_channel.dart';
import 'package:fonix_reference/src/inference_backend.dart';
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

  test('accepts only the two exact Android smoke profiles', () {
    expect(androidSmokeProfileValue, 'cpu');
    expect(AndroidSmokeProfile.parse('cpu'), AndroidSmokeProfile.cpu);
    expect(AndroidSmokeProfile.parse('xnnpack'), AndroidSmokeProfile.xnnpack);
    for (final String invalid in <String>[
      '',
      'CPU',
      'xnnpack ',
      'qnn',
      'windows',
    ]) {
      expect(
        () => AndroidSmokeProfile.parse(invalid),
        throwsA(isA<InferenceBackendFailure>()),
      );
    }
  });

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

    await const AndroidSmokeChannel().completePassed(
      profile: AndroidSmokeProfile.cpu,
      receipt: receipt.toJsonString(),
    );

    expect(received?.method, androidSmokeCompleteMethod);
    final Map<Object?, Object?> arguments =
        (received?.arguments as Map<Object?, Object?>?)!;
    expect(arguments.keys.toSet(), <Object?>{
      'schemaVersion',
      'status',
      'profile',
      'receipt',
    });
    expect(arguments['schemaVersion'], 1);
    expect(arguments['status'], 0);
    expect(arguments['profile'], 'cpu');
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

  test(
    'publishes the selected XNNPACK profile and unchanged receipt',
    () async {
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
      const String receipt =
          '{"schemaVersion":1,"status":"passed","smokeProfile":"xnnpack"}';

      await const AndroidSmokeChannel().completePassed(
        profile: AndroidSmokeProfile.xnnpack,
        receipt: receipt,
      );

      final Map<Object?, Object?> arguments =
          (received?.arguments as Map<Object?, Object?>?)!;
      expect(arguments.keys.toSet(), <Object?>{
        'schemaVersion',
        'status',
        'profile',
        'receipt',
      });
      expect(arguments['schemaVersion'], 1);
      expect(arguments['status'], 0);
      expect(arguments['profile'], 'xnnpack');
      expect(arguments['receipt'], receipt);
    },
  );

  test('rejects out-of-contract receipts before platform invocation', () async {
    final TestDefaultBinaryMessenger messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    var invocationCount = 0;
    messenger.setMockMethodCallHandler(
      const MethodChannel(androidSmokeChannelName),
      (MethodCall call) async {
        invocationCount += 1;
        return null;
      },
    );
    addTearDown(
      () => messenger.setMockMethodCallHandler(
        const MethodChannel(androidSmokeChannelName),
        null,
      ),
    );

    for (final String invalid in <String>[
      '',
      '{}\n',
      '{}\r',
      '{}\u007f',
      '{"status":"passé"}',
      List<String>.filled(16 * 1024 + 1, 'x').join(),
    ]) {
      await expectLater(
        const AndroidSmokeChannel().completePassed(
          profile: AndroidSmokeProfile.xnnpack,
          receipt: invalid,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
    }
    expect(invocationCount, 0);
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
      profile: AndroidSmokeProfile.xnnpack,
      error: StateError('/private/model/path'),
    );

    final Map<Object?, Object?> arguments =
        (received?.arguments as Map<Object?, Object?>?)!;
    expect(arguments.keys.toSet(), <Object?>{
      'schemaVersion',
      'status',
      'profile',
      'receipt',
    });
    expect(arguments['status'], 1);
    expect(arguments['profile'], 'xnnpack');
    final String encoded = arguments['receipt']! as String;
    expect(encoded, isNot(contains('/private/model/path')));
    expect(jsonDecode(encoded), <String, Object?>{
      'schemaVersion': 1,
      'status': 'failed',
      'profile': 'xnnpack',
      'errorType': 'StateError',
    });
  });
}
