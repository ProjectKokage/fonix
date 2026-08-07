import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';

import 'inference_backend.dart';

const int referenceSmokeSchemaVersion = 1;
const String referenceSmokeActivationKey = 'FONIX_REFERENCE_SMOKE';
const String referenceSmokeReceiptPrefix = 'FONIX_REFERENCE_RECEIPT=';
const String referenceSmokeFailurePrefix = 'FONIX_REFERENCE_FAILURE=';
const String residentReferenceLaunchChannelName = 'dev.fonix.reference/launch';
const String residentReferenceLaunchMethod = 'readSmokeActivation';
const String residentReferenceActivationFailureDiagnostic =
    'FONIX_REFERENCE_ACTIVATION_FAILURE';
const String residentReferencePublicationFailureDiagnostic =
    'FONIX_REFERENCE_PUBLICATION_FAILURE';
const int maximumReferenceSmokeReceiptBytes = 16 * 1024;
const int maximumResidentReferenceLineBytes =
    maximumReferenceSmokeReceiptBytes + 256;
const int maximumResidentReferenceProcessId = 0x7fffffff;
const Duration maximumResidentReferenceActivationWait = Duration(seconds: 5);

typedef ResidentReferenceActivationReader = Future<Object?> Function();

/// Whether this process requested the closed desktop one-shot smoke path.
///
/// macOS and Linux share the same receipt contract. Other platforms and every
/// value except the exact ASCII string `1` retain their platform-specific or
/// interactive startup path.
bool desktopReferenceSmokeEnabled({
  required bool isMacOS,
  required bool isLinux,
  required Map<String, String> environment,
}) => isMacOS != isLinux && environment[referenceSmokeActivationKey] == '1';

/// A fresh, path-safe identity for one resident iOS smoke launch.
final class ResidentReferenceChallenge {
  ResidentReferenceChallenge._(this.value);

  factory ResidentReferenceChallenge.parse(String? value) {
    if (value == null || !RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
      throw ArgumentError.value(
        value,
        'value',
        'must be exactly 64 lowercase hexadecimal characters',
      );
    }
    return ResidentReferenceChallenge._(value);
  }

  final String value;

  String get receiptFileName => 'fonix-reference-receipt-$value.txt';

  String get stagingFileName => '$receiptFileName.tmp';
}

/// The bounded file operations owned by one resident receipt publication.
abstract interface class ResidentReferencePublicationIo {
  Future<bool> directoryExists(String path);

  Future<FileSystemEntityType> pathType(String path);

  Future<void> createExclusive(String path);

  Future<ResidentReferenceOpenFile> openWriteOnly(String path);

  Future<void> rename(String from, String to);

  Future<ResidentReferenceFileMetadata> stat(String path);

  Future<void> delete(String path);
}

/// The close-owned write handle returned by [ResidentReferencePublicationIo].
abstract interface class ResidentReferenceOpenFile {
  Future<void> writeFrom(List<int> bytes);

  Future<void> flush();

  Future<void> close();
}

/// The only published-file metadata needed by the atomic receipt contract.
final class ResidentReferenceFileMetadata {
  const ResidentReferenceFileMetadata({required this.type, required this.size});

  final FileSystemEntityType type;
  final int size;
}

/// Reads the one closed activation exposed by the iOS application delegate.
///
/// A null activation preserves the normal interactive application. Platform
/// errors and malformed replies fail closed without exposing native launch
/// values or channel error details.
Future<ResidentReferenceChallenge?> readIosResidentReferenceChallenge({
  ResidentReferenceActivationReader? readActivation,
  Duration timeout = maximumResidentReferenceActivationWait,
}) async {
  if (timeout <= Duration.zero ||
      timeout > maximumResidentReferenceActivationWait) {
    throw ArgumentError.value(timeout, 'timeout');
  }
  final Object? raw;
  try {
    raw = await (readActivation ?? _readIosResidentReferenceActivation)()
        .timeout(timeout);
  } on TimeoutException {
    throw const InferenceBackendFailure(
      summary: 'The iOS reference launch activation timed out.',
      backendUnusable: true,
    );
  } on Object {
    throw const InferenceBackendFailure(
      summary: 'The iOS reference launch activation was unavailable.',
      backendUnusable: true,
    );
  }
  if (raw == null) return null;
  if (raw is! Map<Object?, Object?> ||
      raw.length != 2 ||
      !raw.containsKey('schemaVersion') ||
      !raw.containsKey('challenge') ||
      raw.keys.any(
        (Object? key) => key != 'schemaVersion' && key != 'challenge',
      ) ||
      raw['schemaVersion'] is! int ||
      raw['schemaVersion'] != referenceSmokeSchemaVersion ||
      raw['challenge'] is! String) {
    throw const InferenceBackendFailure(
      summary: 'The iOS reference launch activation was malformed.',
      backendUnusable: true,
    );
  }
  try {
    return ResidentReferenceChallenge.parse(raw['challenge']! as String);
  } on ArgumentError {
    throw const InferenceBackendFailure(
      summary: 'The iOS reference launch activation was malformed.',
      backendUnusable: true,
    );
  }
}

Future<Object?> _readIosResidentReferenceActivation() => const MethodChannel(
  residentReferenceLaunchChannelName,
).invokeMethod<Object?>(residentReferenceLaunchMethod);

/// The path-free, closed receipt shared by the Apple and Android smoke paths.
final class ReferenceSmokeReceipt {
  ReferenceSmokeReceipt._({
    required this.startup,
    required this.outputValues,
    required this.activeProviders,
    required this.fullCpuAssignment,
  });

  factory ReferenceSmokeReceipt.fromRun({
    required InferenceStartupReceipt startup,
    required InferenceRunReceipt run,
  }) {
    if (!_sameDoubles(run.outputValues, referenceOutputValues) ||
        !run.fullAssignment ||
        !_sameStrings(run.activeProviders, const <String>['cpu'])) {
      throw const InferenceBackendFailure(
        summary: 'The packaged smoke receipt did not match its contract.',
      );
    }
    return ReferenceSmokeReceipt._(
      startup: startup,
      outputValues: UnmodifiableListView<int>(<int>[
        for (final double value in run.outputValues) value.toInt(),
      ]),
      activeProviders: UnmodifiableListView<String>(
        List<String>.of(run.activeProviders),
      ),
      fullCpuAssignment: run.fullAssignment,
    );
  }

  final InferenceStartupReceipt startup;
  final List<int> outputValues;
  final List<String> activeProviders;
  final bool fullCpuAssignment;

  Map<String, Object?> toMap() =>
      Map<String, Object?>.unmodifiable(<String, Object?>{
        'schemaVersion': referenceSmokeSchemaVersion,
        'status': 'passed',
        'runtimeVersion': startup.runtimeVersion,
        'runtimeSource': startup.runtimeSource,
        'runtimeOwner': startup.runtimeOwner,
        'artifactFlavor': startup.artifactFlavor,
        'platform': startup.platform,
        'architecture': startup.architecture,
        'shimBuildId': startup.shimBuildId,
        'artifactSha256': startup.artifactSha256,
        'modelSha256': startup.modelSha256,
        'outputValues': outputValues,
        'activeProviders': activeProviders,
        'fullCpuAssignment': fullCpuAssignment,
        'doubleClose': 'passed',
      });

  String toJsonString() {
    final String value = jsonEncode(toMap());
    if (value.contains('\n') ||
        value.contains('\r') ||
        utf8.encode(value).length > maximumReferenceSmokeReceiptBytes) {
      throw const InferenceBackendFailure(
        summary: 'The packaged smoke receipt exceeds its size contract.',
      );
    }
    return value;
  }
}

/// Runs the fixed model once and settles all backend ownership before return.
Future<ReferenceSmokeReceipt> runReferenceSmoke(
  InferenceBackend backend,
) async {
  var closed = false;
  try {
    final InferenceStartupReceipt startup = await backend.start();
    final InferenceRunReceipt run = await backend.run(
      InferenceRequest.reference(),
    );
    final ReferenceSmokeReceipt receipt = ReferenceSmokeReceipt.fromRun(
      startup: startup,
      run: run,
    );
    await backend.close();
    await backend.close();
    closed = true;
    return receipt;
  } finally {
    if (!closed) {
      try {
        await backend.close();
      } on Object {
        // Preserve the authoritative smoke failure.
      }
    }
  }
}

/// Runs the fixed smoke contract without terminating the host process.
///
/// iOS uses this resident path so the Flutter runner remains authoritative for
/// application shutdown. Both callbacks receive one bounded, path-free line.
Future<void> runResidentReferenceSmoke(
  InferenceBackend backend, {
  required ResidentReferenceChallenge challenge,
  required int processId,
  required FutureOr<void> Function(String line) onPassed,
  required FutureOr<void> Function(String line) onFailed,
}) async {
  _validateResidentProcessId(processId);
  final ReferenceSmokeReceipt receipt;
  try {
    receipt = await runReferenceSmoke(backend);
  } on Object catch (error) {
    await onFailed(
      '$referenceSmokeFailurePrefix'
      '${residentReferenceSmokeFailureJson(error, challenge, processId)}',
    );
    return;
  }
  await onPassed(
    '$referenceSmokeReceiptPrefix'
    '${residentReferenceSmokeReceiptJson(receipt, challenge, processId)}',
  );
}

/// Atomically publishes one bounded resident-smoke line in the app sandbox.
///
/// The iOS simulator gate starts from a fresh installation, requires both the
/// staging and final paths to be absent, and reads the final regular file only
/// after this flush-and-rename publication completes.
Future<void> publishResidentReferenceLine(
  String line, {
  required ResidentReferenceChallenge challenge,
  required int processId,
  Directory? directory,
  ResidentReferencePublicationIo? publicationIo,
}) async {
  _validateResidentProcessId(processId);
  if (!_residentLineHasBoundedAsciiEnvelope(line) ||
      !_residentLineMatchesLaunch(line, challenge, processId)) {
    throw const InferenceBackendFailure(
      summary: 'The resident smoke line exceeds its publication contract.',
      backendUnusable: true,
    );
  }
  final List<int> bytes = utf8.encode('$line\n');
  final String outputDirectory = (directory ?? Directory.systemTemp).path;
  final String output = '$outputDirectory/${challenge.receiptFileName}';
  final String staging = '$outputDirectory/${challenge.stagingFileName}';
  final ResidentReferencePublicationIo io =
      publicationIo ?? const _DartResidentReferencePublicationIo();
  ResidentReferenceOpenFile? opened;
  var stagingCreated = false;
  Object? primaryError;
  StackTrace? primaryStackTrace;
  try {
    if (!await io.directoryExists(outputDirectory)) {
      throw const InferenceBackendFailure(
        summary: 'The resident smoke output directory is unavailable.',
        backendUnusable: true,
      );
    }
    if (await io.pathType(output) != FileSystemEntityType.notFound ||
        await io.pathType(staging) != FileSystemEntityType.notFound) {
      throw const InferenceBackendFailure(
        summary: 'The resident smoke output path is not fresh.',
        backendUnusable: true,
      );
    }
    await io.createExclusive(staging);
    stagingCreated = true;
    opened = await io.openWriteOnly(staging);
    await opened.writeFrom(bytes);
    await opened.flush();
    await opened.close();
    opened = null;
    final ResidentReferenceFileMetadata staged = await io.stat(staging);
    if (staged.type != FileSystemEntityType.file ||
        staged.size != bytes.length) {
      throw const InferenceBackendFailure(
        summary: 'The resident smoke output did not stage completely.',
        backendUnusable: true,
      );
    }
    if (await io.pathType(output) != FileSystemEntityType.notFound) {
      throw const InferenceBackendFailure(
        summary: 'The resident smoke output path changed before publication.',
        backendUnusable: true,
      );
    }
    await io.rename(staging, output);
    stagingCreated = false;
  } on Object catch (error, stackTrace) {
    primaryError = error;
    primaryStackTrace = stackTrace;
  }

  Object? cleanupError;
  StackTrace? cleanupStackTrace;
  if (opened != null) {
    try {
      await opened.close();
    } on Object catch (error, stackTrace) {
      cleanupError = error;
      cleanupStackTrace = stackTrace;
    }
  }
  if (stagingCreated) {
    try {
      final FileSystemEntityType type = await io.pathType(staging);
      if (type == FileSystemEntityType.file) {
        await io.delete(staging);
      } else if (type != FileSystemEntityType.notFound) {
        throw const InferenceBackendFailure(
          summary: 'The resident smoke staging path changed during cleanup.',
          backendUnusable: true,
        );
      }
    } on Object catch (error, stackTrace) {
      cleanupError ??= error;
      cleanupStackTrace ??= stackTrace;
    }
  }
  if (primaryError != null) {
    _throwResidentPublicationFailure(primaryError, primaryStackTrace!);
  }
  if (cleanupError != null) {
    _throwResidentPublicationFailure(cleanupError, cleanupStackTrace!);
  }
}

/// Adds the validated launch challenge to one resident success payload.
String residentReferenceSmokeReceiptJson(
  ReferenceSmokeReceipt receipt,
  ResidentReferenceChallenge challenge,
  int processId,
) {
  _validateResidentProcessId(processId);
  final Map<String, Object?> value = <String, Object?>{
    ...receipt.toMap(),
    'challenge': challenge.value,
    'processId': processId,
  };
  return _boundedResidentJson(value);
}

/// Produces the closed challenged failure accepted by resident iOS gates.
String residentReferenceSmokeFailureJson(
  Object error,
  ResidentReferenceChallenge challenge,
  int processId,
) {
  _validateResidentProcessId(processId);
  final String value = jsonEncode(<String, Object?>{
    'schemaVersion': referenceSmokeSchemaVersion,
    'status': 'failed',
    'errorType': _boundedErrorType(error),
    'challenge': challenge.value,
    'processId': processId,
  });
  return _validateResidentJson(value);
}

String _boundedResidentJson(Map<String, Object?> payload) =>
    _validateResidentJson(jsonEncode(payload));

String _validateResidentJson(String value) {
  if (value.contains('\n') ||
      value.contains('\r') ||
      value.codeUnits.any(
        (int codeUnit) => codeUnit < 0x20 || codeUnit > 0x7e,
      ) ||
      value.length > maximumReferenceSmokeReceiptBytes) {
    throw const InferenceBackendFailure(
      summary: 'The resident smoke payload exceeds its size contract.',
      backendUnusable: true,
    );
  }
  return value;
}

bool _residentLineMatchesLaunch(
  String line,
  ResidentReferenceChallenge challenge,
  int processId,
) {
  final String json;
  if (line.startsWith(referenceSmokeReceiptPrefix)) {
    json = line.substring(referenceSmokeReceiptPrefix.length);
  } else if (line.startsWith(referenceSmokeFailurePrefix)) {
    json = line.substring(referenceSmokeFailurePrefix.length);
  } else {
    return false;
  }
  try {
    final Object? decoded = jsonDecode(json);
    return decoded is Map<String, Object?> &&
        decoded['challenge'] == challenge.value &&
        decoded['processId'] == processId;
  } on FormatException {
    return false;
  }
}

void _validateResidentProcessId(int processId) {
  if (processId <= 0 || processId > maximumResidentReferenceProcessId) {
    throw ArgumentError.value(
      processId,
      'processId',
      'must be a positive signed 32-bit process identifier',
    );
  }
}

bool _residentLineHasBoundedAsciiEnvelope(String line) {
  if (line.length >= maximumResidentReferenceLineBytes ||
      (!line.startsWith(referenceSmokeReceiptPrefix) &&
          !line.startsWith(referenceSmokeFailurePrefix))) {
    return false;
  }
  for (var index = 0; index < line.length; index += 1) {
    final int codeUnit = line.codeUnitAt(index);
    if (codeUnit < 0x20 || codeUnit > 0x7e) return false;
  }
  return true;
}

Never _throwResidentPublicationFailure(Object error, StackTrace stackTrace) =>
    Error.throwWithStackTrace(_residentPublicationFailure(error), stackTrace);

InferenceBackendFailure _residentPublicationFailure(Object error) =>
    error is InferenceBackendFailure
    ? error
    : const InferenceBackendFailure(
        summary: 'The resident smoke publication failed.',
        backendUnusable: true,
      );

final class _DartResidentReferencePublicationIo
    implements ResidentReferencePublicationIo {
  const _DartResidentReferencePublicationIo();

  @override
  Future<bool> directoryExists(String path) => Directory(path).exists();

  @override
  Future<FileSystemEntityType> pathType(String path) =>
      FileSystemEntity.type(path, followLinks: false);

  @override
  Future<void> createExclusive(String path) async {
    await File(path).create(exclusive: true);
  }

  @override
  Future<ResidentReferenceOpenFile> openWriteOnly(String path) async =>
      _DartResidentReferenceOpenFile(
        await File(path).open(mode: FileMode.writeOnly),
      );

  @override
  Future<void> rename(String from, String to) async {
    await File(from).rename(to);
  }

  @override
  Future<ResidentReferenceFileMetadata> stat(String path) async {
    final FileStat value = await File(path).stat();
    return ResidentReferenceFileMetadata(type: value.type, size: value.size);
  }

  @override
  Future<void> delete(String path) async {
    await File(path).delete();
  }
}

final class _DartResidentReferenceOpenFile
    implements ResidentReferenceOpenFile {
  _DartResidentReferenceOpenFile(this._file);

  final RandomAccessFile _file;

  @override
  Future<void> writeFrom(List<int> bytes) async {
    await _file.writeFrom(bytes);
  }

  @override
  Future<void> flush() async {
    await _file.flush();
  }

  @override
  Future<void> close() async {
    await _file.close();
  }
}

bool _sameDoubles(List<double> first, List<double> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}

bool _sameStrings(List<String> first, List<String> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
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
