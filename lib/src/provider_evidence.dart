import 'dart:convert';

import 'exceptions.dart';
import 'provider.dart';
import 'strict_json.dart';

/// Maximum accepted UTF-8 bytes in one ORT run-profile evidence payload.
const int ortProviderProfileMaximumBytes = 8 * 1024 * 1024;

/// Maximum number of trace events accepted from one ORT run profile.
const int ortProviderProfileMaximumEvents = 100000;

/// Closed, copied graph-assignment evidence for one completed ORT run.
///
/// This type proves only what the validated profile records. In particular,
/// provider registration and session creation are not treated as evidence that
/// a provider executed a node.
final class OrtProviderRunEvidence {
  OrtProviderRunEvidence._({
    required this.nodeExecutionCount,
    required Map<String, int> nodeExecutionsByProvider,
  }) : nodeExecutionsByProvider = Map<String, int>.unmodifiable(
         nodeExecutionsByProvider,
       );

  /// Parses one complete ORT run-level Chrome-trace JSON payload.
  ///
  /// [reportedProviderNames] maps stable Fonix provider IDs to the exact names
  /// expected in `args.provider`. Unknown provider names fail closed rather
  /// than being silently interpreted as fallback.
  factory OrtProviderRunEvidence.fromOrtProfileJson(
    String source, {
    required Map<String, String> reportedProviderNames,
  }) {
    final int sourceBytes;
    try {
      sourceBytes = utf8.encode(source).length;
    } on FormatException {
      throw const FormatException(
        'The provider profile is not well-formed UTF-8 text.',
      );
    }
    if (sourceBytes == 0 || sourceBytes > ortProviderProfileMaximumBytes) {
      throw const FormatException(
        'The provider profile is empty or exceeds its byte limit.',
      );
    }
    validateStrictJsonInternal(source, label: 'Provider profile');
    final Map<String, String> reportedToWrapper = _validateProviderNameMap(
      reportedProviderNames,
    );
    final Object? decoded;
    try {
      decoded = jsonDecode(source);
    } on FormatException {
      rethrow;
    }
    if (decoded is! List<Object?> ||
        decoded.length > ortProviderProfileMaximumEvents) {
      throw const FormatException(
        'The provider profile must be a bounded trace-event array.',
      );
    }

    var modelRunEvents = 0;
    var nodeExecutionCount = 0;
    _ProfileSpan? modelRunSpan;
    final List<_ProfileSpan> nodeSpans = <_ProfileSpan>[];
    final Map<String, int> counts = <String, int>{};
    for (var index = 0; index < decoded.length; index += 1) {
      final Object? rawEvent = decoded[index];
      if (rawEvent is! Map<Object?, Object?>) {
        throw FormatException('Profile event $index is not an object.');
      }
      final Map<String, Object?> event = _stringKeyedEvent(rawEvent, index);
      final String category = _boundedEventText(
        event['cat'],
        'event[$index].cat',
        maximumBytes: 64,
      );
      final String name = _boundedEventText(
        event['name'],
        'event[$index].name',
        maximumBytes: 1024,
      );
      if (category == 'Session' && name == 'model_run') {
        modelRunEvents += 1;
        modelRunSpan = _profileSpan(event, index);
        continue;
      }
      if (category != 'Node' || !name.endsWith('_kernel_time')) {
        continue;
      }
      nodeSpans.add(_profileSpan(event, index));
      final Object? rawArguments = event['args'];
      if (rawArguments is! Map<Object?, Object?>) {
        throw FormatException('Node event $index has no arguments object.');
      }
      final Map<String, Object?> arguments = _stringKeyedEvent(
        rawArguments,
        index,
      );
      final String reportedName = _boundedEventText(
        arguments['provider'],
        'event[$index].args.provider',
        maximumBytes: 128,
      );
      final String? wrapperId = reportedToWrapper[reportedName];
      if (wrapperId == null) {
        throw FormatException(
          'Node event $index reports an unrecognized execution provider.',
        );
      }
      _validateNodeIndex(arguments['node_index'], index);
      _boundedEventText(
        arguments['op_name'],
        'event[$index].args.op_name',
        maximumBytes: 256,
      );
      nodeExecutionCount += 1;
      counts[wrapperId] = (counts[wrapperId] ?? 0) + 1;
    }
    if (modelRunEvents != 1 || nodeExecutionCount == 0) {
      throw const FormatException(
        'Provider evidence requires exactly one completed non-empty model run.',
      );
    }
    final _ProfileSpan completedRun = modelRunSpan!;
    for (final _ProfileSpan node in nodeSpans) {
      if (node.processId != completedRun.processId ||
          node.start < completedRun.start ||
          node.end > completedRun.end) {
        throw FormatException(
          'Node event ${node.eventIndex} is not contained by the model run.',
        );
      }
    }
    return OrtProviderRunEvidence._(
      nodeExecutionCount: nodeExecutionCount,
      nodeExecutionsByProvider: counts,
    );
  }

  final int nodeExecutionCount;
  final Map<String, int> nodeExecutionsByProvider;

  Set<String> get activeProviderIds => Set<String>.unmodifiable(
    nodeExecutionsByProvider.entries
        .where((MapEntry<String, int> entry) => entry.value > 0)
        .map((MapEntry<String, int> entry) => entry.key),
  );

  bool isActive(String providerId) =>
      (nodeExecutionsByProvider[providerId] ?? 0) > 0;

  bool isFullyAssignedTo(String providerId) =>
      nodeExecutionsByProvider.length == 1 &&
      nodeExecutionsByProvider[providerId] == nodeExecutionCount;

  /// Closed, path-free representation suitable for an isolate receipt.
  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': 1,
    'nodeExecutionCount': nodeExecutionCount,
    'nodeExecutionsByProvider': nodeExecutionsByProvider,
  };

  /// Validates active/full-assignment and fallback requirements after a run.
  ///
  /// Registration requirements remain a session-creation concern. This method
  /// deliberately evaluates only requirements that need node evidence.
  void enforce({
    required List<OrtExecutionProvider> providers,
    required OrtFallbackPolicy fallbackPolicy,
  }) {
    if (providers.isEmpty) {
      throw ArgumentError('Provider evidence requires an explicit policy.');
    }
    for (final OrtExecutionProvider provider in providers) {
      switch (provider.requirement) {
        case OrtProviderRequirement.preferred:
        case OrtProviderRequirement.required:
          break;
        case OrtProviderRequirement.requireActive:
          if (!isActive(provider.id)) {
            throw OrtProviderEvidenceException(
              message:
                  'Required provider "${provider.id}" executed no profiled '
                  'node.',
              context: <String, Object?>{'providerId': provider.id},
            );
          }
        case OrtProviderRequirement.requireFullAssignment:
          if (!isFullyAssignedTo(provider.id)) {
            throw OrtProviderEvidenceException(
              message:
                  'Required provider "${provider.id}" did not execute the '
                  'full profiled graph.',
              context: <String, Object?>{'providerId': provider.id},
            );
          }
      }
    }

    final List<String> orderedIds = <String>[
      for (final OrtExecutionProvider provider in providers) provider.id,
    ];
    final String highestPriority = orderedIds.first;
    switch (fallbackPolicy) {
      case OrtFallbackPolicy.allow:
      case OrtFallbackPolicy.report:
        return;
      case OrtFallbackPolicy.rejectCpu:
        if (highestPriority != 'cpu' && isActive('cpu')) {
          throw OrtProviderEvidenceException(
            message: 'CPU fallback occurred during a profiled accelerator run.',
          );
        }
      case OrtFallbackPolicy.rejectAny:
        if (!isFullyAssignedTo(highestPriority)) {
          throw OrtProviderEvidenceException(
            message:
                'The profiled graph fell back from highest-priority provider '
                '"$highestPriority".',
            context: <String, Object?>{'providerId': highestPriority},
          );
        }
    }
  }
}

/// Reconstructs a validated isolate receipt without weakening profile parsing.
///
/// This package-internal helper is hidden from `package:fonix/fonix.dart`.
OrtProviderRunEvidence providerRunEvidenceFromCountsInternal({
  required int nodeExecutionCount,
  required Map<String, int> nodeExecutionsByProvider,
}) {
  if (nodeExecutionCount <= 0 ||
      nodeExecutionCount > ortProviderProfileMaximumEvents ||
      nodeExecutionsByProvider.isEmpty ||
      nodeExecutionsByProvider.length > 16) {
    throw const FormatException('Provider evidence counts are out of bounds.');
  }
  var sum = 0;
  final Map<String, int> copied = <String, int>{};
  for (final MapEntry<String, int> entry in nodeExecutionsByProvider.entries) {
    if (!RegExp(r'^[a-z][a-z0-9_-]{0,63}$').hasMatch(entry.key) ||
        entry.value <= 0 ||
        entry.value > ortProviderProfileMaximumEvents) {
      throw const FormatException('Provider evidence contains invalid counts.');
    }
    sum += entry.value;
    if (sum > ortProviderProfileMaximumEvents) {
      throw const FormatException('Provider evidence counts exceed the limit.');
    }
    copied[entry.key] = entry.value;
  }
  if (sum != nodeExecutionCount) {
    throw const FormatException('Provider evidence counts are inconsistent.');
  }
  return OrtProviderRunEvidence._(
    nodeExecutionCount: nodeExecutionCount,
    nodeExecutionsByProvider: copied,
  );
}

final class _ProfileSpan {
  const _ProfileSpan({
    required this.eventIndex,
    required this.processId,
    required this.start,
    required this.end,
  });

  final int eventIndex;
  final int processId;
  final int start;
  final int end;
}

_ProfileSpan _profileSpan(Map<String, Object?> event, int eventIndex) {
  if (event['ph'] != 'X') {
    throw FormatException(
      'Profile event $eventIndex is not a complete-duration event.',
    );
  }
  final int processId = _nonNegativeTraceInteger(
    event['pid'],
    'event[$eventIndex].pid',
  );
  _nonNegativeTraceInteger(event['tid'], 'event[$eventIndex].tid');
  final int start = _nonNegativeTraceInteger(
    event['ts'],
    'event[$eventIndex].ts',
  );
  final int duration = _nonNegativeTraceInteger(
    event['dur'],
    'event[$eventIndex].dur',
  );
  if (start > 0x7fffffffffffffff - duration) {
    throw FormatException('Profile event $eventIndex has an invalid duration.');
  }
  return _ProfileSpan(
    eventIndex: eventIndex,
    processId: processId,
    start: start,
    end: start + duration,
  );
}

int _nonNegativeTraceInteger(Object? value, String field) {
  if (value is! int || value < 0 || value > 0x7fffffffffffffff) {
    throw FormatException('$field is not a bounded non-negative integer.');
  }
  return value;
}

Map<String, String> _validateProviderNameMap(Map<String, String> source) {
  if (source.isEmpty || source.length > 16) {
    throw ArgumentError('reportedProviderNames must contain 1 to 16 entries.');
  }
  final Map<String, String> reportedToWrapper = <String, String>{};
  final RegExp wrapperId = RegExp(r'^[a-z][a-z0-9_-]{0,63}$');
  final RegExp reportedName = RegExp(r'^[A-Za-z][A-Za-z0-9_]{0,127}$');
  for (final MapEntry<String, String> entry in source.entries) {
    if (!wrapperId.hasMatch(entry.key) ||
        !reportedName.hasMatch(entry.value) ||
        reportedToWrapper.containsKey(entry.value)) {
      throw ArgumentError(
        'reportedProviderNames must be unique bounded provider identities.',
      );
    }
    reportedToWrapper[entry.value] = entry.key;
  }
  return reportedToWrapper;
}

Map<String, Object?> _stringKeyedEvent(
  Map<Object?, Object?> source,
  int index,
) {
  final Map<String, Object?> result = <String, Object?>{};
  for (final MapEntry<Object?, Object?> entry in source.entries) {
    if (entry.key is! String) {
      throw FormatException('Profile event $index contains a non-string key.');
    }
    result[entry.key! as String] = entry.value;
  }
  return result;
}

String _boundedEventText(
  Object? value,
  String field, {
  required int maximumBytes,
}) {
  if (value is! String ||
      value.isEmpty ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r') ||
      utf8.encode(value).length > maximumBytes) {
    throw FormatException('$field is not bounded safe text.');
  }
  return value;
}

void _validateNodeIndex(Object? value, int eventIndex) {
  if (value is int && value >= 0) return;
  if (value is String && RegExp(r'^(0|[1-9][0-9]{0,9})$').hasMatch(value)) {
    return;
  }
  throw FormatException('Node event $eventIndex has an invalid node index.');
}
