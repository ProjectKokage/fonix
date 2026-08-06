import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';

import 'harness_contract.dart';

const String harnessChannelName = 'dev.fonix.sherpa_reference/harness';
const String harnessReadLaunchMethod = 'readLaunch';
const String harnessCompleteMethod = 'complete';

final class HarnessChannel {
  const HarnessChannel({
    MethodChannel channel = const MethodChannel(harnessChannelName),
  }) : _channel = channel;

  final MethodChannel _channel;

  Future<HarnessLaunch> readLaunch() async {
    final Object? value = await _channel.invokeMethod<Object?>(
      harnessReadLaunchMethod,
    );
    return HarnessLaunch.fromPlatform(value);
  }

  Future<void> complete(HarnessCompletionPayload payload) async {
    if (!isLowercaseSha256(payload.launchChallengeSha256)) {
      throw const HarnessContractException('invalid-launch-challenge-hash');
    }
    final String payloadJson = encodeHarnessPayload(payload);
    final String payloadSha256 = sha256
        .convert(utf8.encode(payloadJson))
        .toString();
    await _channel.invokeMethod<void>(harnessCompleteMethod, <String, Object?>{
      'schemaVersion': harnessWireSchemaVersion,
      'status': payload.status.wireValue,
      'payloadSha256': payloadSha256,
      'payloadJson': payloadJson,
    });
  }
}
