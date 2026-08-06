import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

const int harnessWireSchemaVersion = 1;
const int maximumLaunchChallengeBytes = 1024;
const int maximumCompletionPayloadBytes = 512 * 1024;

enum HarnessLoadOrder {
  dartFirst('dart-first'),
  sherpaFirst('sherpa-first');

  const HarnessLoadOrder(this.wireValue);

  final String wireValue;

  static HarnessLoadOrder parse(String value) {
    for (final HarnessLoadOrder order in values) {
      if (order.wireValue == value) return order;
    }
    throw const HarnessContractException('unsupported-load-order');
  }
}

enum HarnessCompletionStatus {
  passed('passed'),
  unavailable('unavailable'),
  failed('failed');

  const HarnessCompletionStatus(this.wireValue);

  final String wireValue;
}

enum HarnessCompletionReason {
  nativeFixturesUnprovisioned('native-fixtures-unprovisioned'),
  qualificationFailed('qualification-failed');

  const HarnessCompletionReason(this.wireValue);

  final String wireValue;
}

final class HarnessLaunch {
  HarnessLaunch._({
    required this.loadOrder,
    required this.challengeBase64,
    required this.launchChallengeSha256,
  });

  factory HarnessLaunch.fromPlatform(Object? value) {
    final Map<Object?, Object?> map = _closedMap(value, const <String>{
      'schemaVersion',
      'loadOrder',
      'launchChallengeBase64',
    }, 'invalid-launch-envelope');
    if (map['schemaVersion'] != harnessWireSchemaVersion) {
      throw const HarnessContractException('invalid-launch-schema');
    }
    final Object? rawOrder = map['loadOrder'];
    final Object? rawChallenge = map['launchChallengeBase64'];
    if (rawOrder is! String || rawChallenge is! String) {
      throw const HarnessContractException('invalid-launch-field-type');
    }
    if (rawChallenge.isEmpty ||
        rawChallenge.length > ((maximumLaunchChallengeBytes + 2) ~/ 3) * 4 ||
        !_isPrintableAscii(rawChallenge)) {
      throw const HarnessContractException('invalid-launch-challenge');
    }

    final Uint8List challenge;
    try {
      challenge = base64Decode(rawChallenge);
    } on FormatException {
      throw const HarnessContractException('invalid-launch-challenge');
    }
    if (challenge.isEmpty ||
        challenge.length > maximumLaunchChallengeBytes ||
        base64Encode(challenge) != rawChallenge) {
      throw const HarnessContractException('invalid-launch-challenge');
    }

    return HarnessLaunch._(
      loadOrder: HarnessLoadOrder.parse(rawOrder),
      challengeBase64: rawChallenge,
      launchChallengeSha256: sha256.convert(challenge).toString(),
    );
  }

  final HarnessLoadOrder loadOrder;
  final String challengeBase64;
  final String launchChallengeSha256;
}

abstract interface class HarnessCompletionPayload {
  HarnessCompletionStatus get status;
  HarnessLoadOrder get loadOrder;
  String get launchChallengeSha256;
  Map<String, Object?> toJson();
}

final class HarnessUnavailableResult implements HarnessCompletionPayload {
  const HarnessUnavailableResult._({
    required this.status,
    required this.loadOrder,
    required this.launchChallengeSha256,
    required this.reason,
  });

  factory HarnessUnavailableResult.nativeFixtures(HarnessLaunch launch) =>
      HarnessUnavailableResult._(
        status: HarnessCompletionStatus.unavailable,
        loadOrder: launch.loadOrder,
        launchChallengeSha256: launch.launchChallengeSha256,
        reason: HarnessCompletionReason.nativeFixturesUnprovisioned,
      );

  factory HarnessUnavailableResult.qualificationFailed(HarnessLaunch launch) =>
      HarnessUnavailableResult._(
        status: HarnessCompletionStatus.failed,
        loadOrder: launch.loadOrder,
        launchChallengeSha256: launch.launchChallengeSha256,
        reason: HarnessCompletionReason.qualificationFailed,
      );

  @override
  final HarnessCompletionStatus status;

  @override
  final HarnessLoadOrder loadOrder;

  @override
  final String launchChallengeSha256;

  final HarnessCompletionReason reason;

  @override
  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': harnessWireSchemaVersion,
    'result': status.wireValue,
    'launchChallengeSha256': launchChallengeSha256,
    'loadOrder': loadOrder.wireValue,
    'reason': reason.wireValue,
  };
}

String encodeHarnessPayload(HarnessCompletionPayload payload) {
  final String encoded = jsonEncode(payload.toJson());
  final int byteLength = utf8.encode(encoded).length;
  if (byteLength == 0 ||
      byteLength > maximumCompletionPayloadBytes ||
      !_isPrintableAscii(encoded)) {
    throw const HarnessContractException('invalid-completion-payload');
  }
  return encoded;
}

bool isLowercaseSha256(String value) =>
    RegExp(r'^[0-9a-f]{64}$').hasMatch(value);

Map<Object?, Object?> _closedMap(
  Object? value,
  Set<String> expectedKeys,
  String errorCode,
) {
  if (value is! Map<Object?, Object?> ||
      value.length != expectedKeys.length ||
      !value.keys.every((Object? key) => key is String) ||
      !expectedKeys.every(value.containsKey)) {
    throw HarnessContractException(errorCode);
  }
  return value;
}

bool _isPrintableAscii(String value) => value.codeUnits.every(
  (int codeUnit) => codeUnit >= 0x20 && codeUnit <= 0x7e,
);

final class HarnessContractException implements Exception {
  const HarnessContractException(this.code);

  final String code;

  @override
  String toString() => 'HarnessContractException($code)';
}
