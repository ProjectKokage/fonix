import 'dart:convert';

import 'package:flutter/services.dart';

import 'inference_backend.dart';
import 'reference_smoke.dart';

const String androidSmokeDefine = 'FONIX_REFERENCE_SMOKE';
const bool androidSmokeEnabled = bool.fromEnvironment(androidSmokeDefine);
const String androidSmokeChannelName = 'dev.fonix.reference/smoke';
const String androidSmokeCompleteMethod = 'complete';

/// A versioned, bounded Dart-to-MainActivity smoke completion contract.
final class AndroidSmokeChannel {
  const AndroidSmokeChannel({
    MethodChannel channel = const MethodChannel(androidSmokeChannelName),
  }) : _channel = channel;

  final MethodChannel _channel;

  Future<void> completePassed(ReferenceSmokeReceipt receipt) =>
      _complete(status: 0, receipt: receipt.toJsonString());

  Future<void> completeFailed(Object error) => _complete(
    status: 1,
    receipt: jsonEncode(<String, Object?>{
      'schemaVersion': referenceSmokeSchemaVersion,
      'status': 'failed',
      'errorType': _boundedErrorType(error),
    }),
  );

  Future<void> _complete({required int status, required String receipt}) async {
    if (status < 0 ||
        status > 1 ||
        receipt.contains('\n') ||
        receipt.contains('\r') ||
        utf8.encode(receipt).length > maximumReferenceSmokeReceiptBytes) {
      throw const InferenceBackendFailure(
        summary: 'The Android smoke completion exceeds its contract.',
        backendUnusable: true,
      );
    }
    await _channel
        .invokeMethod<void>(androidSmokeCompleteMethod, <String, Object?>{
          'schemaVersion': referenceSmokeSchemaVersion,
          'status': status,
          'receipt': receipt,
        });
  }
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
