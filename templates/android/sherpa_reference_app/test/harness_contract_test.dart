import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/harness_channel.dart';
import 'package:fonix_sherpa_reference/src/harness_contract.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('accepts only the exact canonical launch envelope', () {
    final HarnessLaunch launch = HarnessLaunch.fromPlatform(
      _launchMap(HarnessLoadOrder.dartFirst),
    );
    expect(launch.loadOrder, HarnessLoadOrder.dartFirst);
    expect(launch.challengeBase64, base64Encode(utf8.encode('nonce-0001\n')));
    expect(
      launch.launchChallengeSha256,
      sha256.convert(utf8.encode('nonce-0001\n')).toString(),
    );

    for (final Object? invalid in <Object?>[
      null,
      <String, Object?>{},
      <String, Object?>{..._launchMap(HarnessLoadOrder.dartFirst), 'uid': 1},
      <String, Object?>{
        ..._launchMap(HarnessLoadOrder.dartFirst),
        'schemaVersion': '1',
      },
      <String, Object?>{
        ..._launchMap(HarnessLoadOrder.dartFirst),
        'loadOrder': 'Dart-first',
      },
      <String, Object?>{
        ..._launchMap(HarnessLoadOrder.dartFirst),
        'launchChallengeBase64': 'bm9uY2U',
      },
    ]) {
      expect(
        () => HarnessLaunch.fromPlatform(invalid),
        throwsA(isA<HarnessContractException>()),
      );
    }
  });

  test('shares the validator challenge bound', () {
    expect(maximumLaunchChallengeBytes, 1024);
    expect(
      () => HarnessLaunch.fromPlatform(<String, Object?>{
        ..._launchMap(HarnessLoadOrder.dartFirst),
        'launchChallengeBase64': base64Encode(
          Uint8List(maximumLaunchChallengeBytes + 1),
        ),
      }),
      throwsA(isA<HarnessContractException>()),
    );
  });

  test('publishes the exact unavailable completion envelope', () async {
    final TestDefaultBinaryMessenger messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    MethodCall? completion;
    messenger.setMockMethodCallHandler(
      const MethodChannel(harnessChannelName),
      (MethodCall call) async {
        if (call.method == harnessReadLaunchMethod) {
          return _launchMap(HarnessLoadOrder.sherpaFirst);
        }
        completion = call;
        return null;
      },
    );
    addTearDown(
      () => messenger.setMockMethodCallHandler(
        const MethodChannel(harnessChannelName),
        null,
      ),
    );

    const HarnessChannel channel = HarnessChannel();
    final HarnessLaunch launch = await channel.readLaunch();
    await channel.complete(HarnessUnavailableResult.nativeFixtures(launch));

    expect(completion?.method, harnessCompleteMethod);
    final Map<Object?, Object?> arguments =
        completion?.arguments! as Map<Object?, Object?>;
    expect(arguments.keys.toSet(), <Object?>{
      'schemaVersion',
      'status',
      'payloadSha256',
      'payloadJson',
    });
    expect(arguments['schemaVersion'], 1);
    expect(arguments['status'], 'unavailable');
    final String payloadJson = arguments['payloadJson']! as String;
    expect(
      arguments['payloadSha256'],
      sha256.convert(utf8.encode(payloadJson)).toString(),
    );
    expect(jsonDecode(payloadJson), <String, Object?>{
      'schemaVersion': 1,
      'result': 'unavailable',
      'launchChallengeSha256': launch.launchChallengeSha256,
      'loadOrder': 'sherpa-first',
      'reason': 'native-fixtures-unprovisioned',
    });
    expect(payloadJson, isNot(contains('\n')));
    expect(payloadJson, isNot(contains('\r')));
    expect(payloadJson, isNot(contains('uid')));
    expect(payloadJson, isNot(contains('pid')));
  });
}

Map<String, Object?> _launchMap(HarnessLoadOrder order) => <String, Object?>{
  'schemaVersion': harnessWireSchemaVersion,
  'loadOrder': order.wireValue,
  'launchChallengeBase64': base64Encode(utf8.encode('nonce-0001\n')),
};
