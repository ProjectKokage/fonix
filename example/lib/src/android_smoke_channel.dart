import 'dart:convert';

import 'package:flutter/services.dart';

import 'inference_backend.dart';
import 'reference_smoke.dart';

const String androidSmokeDefine = referenceSmokeActivationKey;
const bool androidSmokeEnabled = bool.fromEnvironment(androidSmokeDefine);
const String androidSmokeProfileDefine = 'FONIX_REFERENCE_PROFILE';
const String androidSmokeProfileValue = String.fromEnvironment(
  androidSmokeProfileDefine,
  defaultValue: 'cpu',
);
const String androidSmokeChannelName = 'dev.fonix.reference/smoke';
const String androidSmokeCompleteMethod = 'complete';

/// The closed Android reference-app smoke profiles accepted by the gate.
enum AndroidSmokeProfile {
  cpu('cpu'),
  xnnpack('xnnpack');

  const AndroidSmokeProfile(this.wireValue);

  final String wireValue;

  static AndroidSmokeProfile parse(String value) {
    for (final AndroidSmokeProfile profile in values) {
      if (profile.wireValue == value) return profile;
    }
    throw const InferenceBackendFailure(
      summary: 'The Android smoke profile is unsupported.',
      backendUnusable: true,
    );
  }
}

/// A versioned, bounded Dart-to-MainActivity smoke completion contract.
final class AndroidSmokeChannel {
  const AndroidSmokeChannel({
    MethodChannel channel = const MethodChannel(androidSmokeChannelName),
  }) : _channel = channel;

  final MethodChannel _channel;

  Future<void> completePassed({
    required AndroidSmokeProfile profile,
    required String receipt,
  }) => _complete(status: 0, profile: profile, receipt: receipt);

  Future<void> completeFailed({
    required AndroidSmokeProfile profile,
    required Object error,
  }) => _complete(
    status: 1,
    profile: profile,
    receipt: jsonEncode(<String, Object?>{
      'schemaVersion': referenceSmokeSchemaVersion,
      'status': 'failed',
      'profile': profile.wireValue,
      'errorType': _boundedErrorType(error),
    }),
  );

  Future<void> _complete({
    required int status,
    required AndroidSmokeProfile profile,
    required String receipt,
  }) async {
    if (status < 0 || status > 1 || !_isBoundedPrintableAscii(receipt)) {
      throw const InferenceBackendFailure(
        summary: 'The Android smoke completion exceeds its contract.',
        backendUnusable: true,
      );
    }
    await _channel
        .invokeMethod<void>(androidSmokeCompleteMethod, <String, Object?>{
          'schemaVersion': referenceSmokeSchemaVersion,
          'status': status,
          'profile': profile.wireValue,
          'receipt': receipt,
        });
  }
}

bool _isBoundedPrintableAscii(String value) {
  if (value.isEmpty || value.length > maximumReferenceSmokeReceiptBytes) {
    return false;
  }
  return value.codeUnits.every(
    (int codeUnit) => codeUnit >= 0x20 && codeUnit <= 0x7e,
  );
}

String _boundedErrorType(Object error) {
  final String value = error.runtimeType.toString();
  if (value.isEmpty ||
      value.length > 64 ||
      !RegExp(r'^[A-Za-z][A-Za-z0-9_.]*$').hasMatch(value)) {
    return 'Object';
  }
  return value;
}
