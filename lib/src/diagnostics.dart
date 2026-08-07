import 'dart:convert';

import 'resource_limits.dart';
import 'strict_json.dart';

enum OrtRuntimeOwner { wrapper, sherpa, application, system }

/// Runtime provenance reported by the built shim.
///
/// `aligned` describes application packaging; it is not a loader source.
enum OrtRuntimeMode { linked, bundled, process, aligned, file }

enum OrtProviderRegistrationMechanism {
  implicit,
  generic,
  providerSpecific,
  plugin,
  unknown,
}

/// One provider's distinct build, discovery, registration, and use states.
final class OrtProviderDiagnostics {
  OrtProviderDiagnostics({
    required this.wrapperId,
    required this.registrationMechanism,
    required this.registrationName,
    required this.reportedName,
    required this.compiled,
    required this.discoverable,
    required this.registered,
    required this.active,
    required this.qualified,
    required Map<String, String> options,
    this.assignmentEvidence,
    this.fallbackReason,
  }) : options = Map<String, String>.unmodifiable(options);

  final String wrapperId;
  final OrtProviderRegistrationMechanism registrationMechanism;
  final String? registrationName;
  final String? reportedName;
  final bool? compiled;
  final bool? discoverable;
  final bool? registered;
  final bool? active;
  final bool? qualified;
  final String? assignmentEvidence;
  final String? fallbackReason;

  /// Normalized options with path-, cache-, and secret-bearing values redacted.
  final Map<String, String> options;

  Map<String, Object?> toJson() => <String, Object?>{
    'wrapperId': wrapperId,
    'registrationMechanism': switch (registrationMechanism) {
      OrtProviderRegistrationMechanism.implicit => 'implicit',
      OrtProviderRegistrationMechanism.generic => 'generic',
      OrtProviderRegistrationMechanism.providerSpecific => 'provider-specific',
      OrtProviderRegistrationMechanism.plugin => 'plugin',
      OrtProviderRegistrationMechanism.unknown => 'unknown',
    },
    'registrationName': registrationName,
    'reportedName': reportedName,
    'compiled': compiled,
    'discoverable': discoverable,
    'registered': registered,
    'active': active,
    'qualified': qualified,
    'assignmentEvidence': assignmentEvidence,
    'fallbackReason': fallbackReason,
    'options': options,
  };
}

final class OrtSessionDiagnostics {
  const OrtSessionDiagnostics({
    required this.executionMode,
    required this.graphOptimization,
    required this.intraOpThreads,
    required this.interOpThreads,
    required this.memoryPattern,
    required this.fallbackPolicy,
  });

  final String executionMode;
  final String graphOptimization;
  final int intraOpThreads;
  final int interOpThreads;
  final bool memoryPattern;
  final String fallbackPolicy;

  Map<String, Object?> toJson() => <String, Object?>{
    'executionMode': executionMode,
    'graphOptimization': graphOptimization,
    'intraOpThreads': intraOpThreads,
    'interOpThreads': interOpThreads,
    'memoryPattern': memoryPattern,
    'fallbackPolicy': fallbackPolicy,
  };
}

/// A bounded, redacted snapshot of the active native/runtime/session state.
final class OrtDiagnostics {
  OrtDiagnostics({
    required this.schemaVersion,
    required this.dartPackageVersion,
    required this.shimAbiVersion,
    required this.shimBuildId,
    required this.requiredOrtApiVersion,
    required this.negotiatedOrtApiVersion,
    required this.runtimeVersion,
    required this.runtimeOwner,
    required this.runtimeMode,
    required this.runtimeIdentity,
    required this.platform,
    required this.architecture,
    required this.artifactFlavor,
    required this.artifactSha256,
    required List<OrtProviderDiagnostics> providers,
    this.modelId,
    this.session,
  }) : providers = List<OrtProviderDiagnostics>.unmodifiable(providers);

  factory OrtDiagnostics.fromJsonString(
    String source, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    if (source.length > limits.maxDiagnosticsBytes ||
        utf8.encode(source).length > limits.maxDiagnosticsBytes) {
      throw const FormatException(
        'Diagnostics payload exceeds its byte limit.',
      );
    }
    validateStrictJsonInternal(source, label: 'Diagnostics');
    final decoded = jsonDecode(source);
    if (decoded is! Map) {
      throw const FormatException('Diagnostics must be a JSON object.');
    }
    return OrtDiagnostics.fromJson(
      decoded.cast<Object?, Object?>(),
      limits: limits,
    );
  }

  factory OrtDiagnostics.fromJson(
    Map<Object?, Object?> source, {
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final json = _stringKeyed(
      source,
      'diagnostics',
      maximumEntries: _diagnosticKeys.length,
    );
    _exactKeys(json, _diagnosticKeys, 'diagnostics');
    final schemaVersion = _integer(json, 'schemaVersion');
    if (schemaVersion != 1) {
      throw FormatException('Unsupported diagnostics schema $schemaVersion.');
    }
    final rawProviders = _list(json, 'providers');
    if (rawProviders.length > limits.maxProviders) {
      throw const FormatException('Diagnostics contains too many providers.');
    }
    final providers = <OrtProviderDiagnostics>[];
    for (var index = 0; index < rawProviders.length; index += 1) {
      final value = rawProviders[index];
      if (value is! Map) {
        throw FormatException('providers[$index] must be an object.');
      }
      providers.add(_provider(value.cast<Object?, Object?>(), limits, index));
    }
    final rawSession = json['session'];
    OrtSessionDiagnostics? session;
    if (rawSession != null) {
      if (rawSession is! Map) {
        throw const FormatException('session must be an object or null.');
      }
      session = _session(rawSession.cast<Object?, Object?>());
    }
    return OrtDiagnostics(
      schemaVersion: schemaVersion,
      dartPackageVersion: _text(json, 'dartPackageVersion', 64),
      shimAbiVersion: _positiveInteger(json, 'shimAbiVersion'),
      shimBuildId: _text(json, 'shimBuildId', 128),
      requiredOrtApiVersion: _positiveInteger(json, 'requiredOrtApiVersion'),
      negotiatedOrtApiVersion: _positiveInteger(
        json,
        'negotiatedOrtApiVersion',
      ),
      runtimeVersion: _text(json, 'runtimeVersion', 128),
      runtimeOwner: _enumValue(
        json,
        'runtimeOwner',
        OrtRuntimeOwner.values,
        (value) => value.name,
      ),
      runtimeMode: _enumValue(
        json,
        'runtimeMode',
        OrtRuntimeMode.values,
        (value) => value.name,
      ),
      runtimeIdentity: _text(json, 'runtimeIdentity', 256),
      platform: _text(json, 'platform', 64),
      architecture: _text(json, 'architecture', 64),
      artifactFlavor: _text(json, 'artifactFlavor', 64),
      artifactSha256: _nullableDigest(json, 'artifactSha256'),
      providers: providers,
      modelId: _nullableText(json, 'modelId', 256),
      session: session,
    );
  }

  final int schemaVersion;
  final String dartPackageVersion;
  final int shimAbiVersion;
  final String shimBuildId;
  final int requiredOrtApiVersion;
  final int negotiatedOrtApiVersion;
  final String runtimeVersion;
  final OrtRuntimeOwner runtimeOwner;
  final OrtRuntimeMode runtimeMode;

  /// A canonical token or non-reversible digest, never a private path.
  final String runtimeIdentity;
  final String platform;
  final String architecture;
  final String artifactFlavor;

  /// Lock-selected source artifact digest, or null for an external runtime.
  final String? artifactSha256;
  final String? modelId;
  final OrtSessionDiagnostics? session;
  final List<OrtProviderDiagnostics> providers;

  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': schemaVersion,
    'dartPackageVersion': dartPackageVersion,
    'shimAbiVersion': shimAbiVersion,
    'shimBuildId': shimBuildId,
    'requiredOrtApiVersion': requiredOrtApiVersion,
    'negotiatedOrtApiVersion': negotiatedOrtApiVersion,
    'runtimeVersion': runtimeVersion,
    'runtimeOwner': runtimeOwner.name,
    'runtimeMode': runtimeMode.name,
    'runtimeIdentity': runtimeIdentity,
    'platform': platform,
    'architecture': architecture,
    'artifactFlavor': artifactFlavor,
    'artifactSha256': artifactSha256,
    'modelId': modelId,
    'session': session?.toJson(),
    'providers': <Object?>[for (final provider in providers) provider.toJson()],
  };
}

const Set<String> _diagnosticKeys = <String>{
  'schemaVersion',
  'dartPackageVersion',
  'shimAbiVersion',
  'shimBuildId',
  'requiredOrtApiVersion',
  'negotiatedOrtApiVersion',
  'runtimeVersion',
  'runtimeOwner',
  'runtimeMode',
  'runtimeIdentity',
  'platform',
  'architecture',
  'artifactFlavor',
  'artifactSha256',
  'modelId',
  'session',
  'providers',
};

const Set<String> _providerKeys = <String>{
  'wrapperId',
  'registrationMechanism',
  'registrationName',
  'reportedName',
  'compiled',
  'discoverable',
  'registered',
  'active',
  'qualified',
  'assignmentEvidence',
  'fallbackReason',
  'options',
};

const Set<String> _sessionKeys = <String>{
  'executionMode',
  'graphOptimization',
  'intraOpThreads',
  'interOpThreads',
  'memoryPattern',
  'fallbackPolicy',
};

OrtProviderDiagnostics _provider(
  Map<Object?, Object?> source,
  OrtResourceLimits limits,
  int index,
) {
  final json = _stringKeyed(
    source,
    'providers[$index]',
    maximumEntries: _providerKeys.length,
  );
  _exactKeys(json, _providerKeys, 'providers[$index]');
  final rawOptions = json['options'];
  if (rawOptions is! Map) {
    throw FormatException('providers[$index].options must be an object.');
  }
  final optionMap = _stringKeyed(
    rawOptions.cast<Object?, Object?>(),
    'providers[$index].options',
    maximumEntries: limits.maxProviderOptions,
  );
  final options = <String, String>{};
  for (final entry in optionMap.entries) {
    if (entry.value is! String) {
      throw FormatException('providers[$index] has an invalid option.');
    }
    final value = entry.value! as String;
    _providerOptionText(entry.key, 'providers[$index] option key', 128);
    _providerOptionText(
      value,
      'providers[$index] option value',
      4096,
      allowEmpty: true,
    );
    options[entry.key] = value;
  }
  return OrtProviderDiagnostics(
    wrapperId: _text(json, 'wrapperId', 64),
    registrationMechanism: _enumValue(
      json,
      'registrationMechanism',
      OrtProviderRegistrationMechanism.values,
      (value) => switch (value) {
        OrtProviderRegistrationMechanism.providerSpecific =>
          'provider-specific',
        _ => value.name,
      },
    ),
    registrationName: _nullableText(json, 'registrationName', 128),
    reportedName: _nullableText(json, 'reportedName', 128),
    compiled: _nullableBool(json, 'compiled'),
    discoverable: _nullableBool(json, 'discoverable'),
    registered: _nullableBool(json, 'registered'),
    active: _nullableBool(json, 'active'),
    qualified: _nullableBool(json, 'qualified'),
    assignmentEvidence: _nullableText(json, 'assignmentEvidence', 256),
    fallbackReason: _nullableText(json, 'fallbackReason', 512),
    options: options,
  );
}

OrtSessionDiagnostics _session(Map<Object?, Object?> source) {
  final json = _stringKeyed(
    source,
    'session',
    maximumEntries: _sessionKeys.length,
  );
  _exactKeys(json, _sessionKeys, 'session');
  return OrtSessionDiagnostics(
    executionMode: _text(json, 'executionMode', 32),
    graphOptimization: _text(json, 'graphOptimization', 32),
    intraOpThreads: _integer(json, 'intraOpThreads'),
    interOpThreads: _integer(json, 'interOpThreads'),
    memoryPattern: _bool(json, 'memoryPattern'),
    fallbackPolicy: _text(json, 'fallbackPolicy', 32),
  );
}

Map<String, Object?> _stringKeyed(
  Map<Object?, Object?> source,
  String field, {
  required int maximumEntries,
}) {
  if (source.length > maximumEntries) {
    throw FormatException('$field contains too many entries.');
  }
  final result = <String, Object?>{};
  var count = 0;
  for (final entry in source.entries) {
    if (count == maximumEntries) {
      throw FormatException('$field contains too many entries.');
    }
    count += 1;
    if (entry.key is! String) {
      throw FormatException('$field contains a non-string key.');
    }
    result[entry.key! as String] = entry.value;
  }
  return result;
}

void _providerOptionText(
  String value,
  String field,
  int maximumBytes, {
  bool allowEmpty = false,
}) {
  if ((!allowEmpty && value.isEmpty) ||
      value.length > maximumBytes ||
      utf8.encode(value).length > maximumBytes ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r')) {
    throw FormatException('$field must be bounded UTF-8 text.');
  }
}

void _exactKeys(Map<String, Object?> json, Set<String> keys, String field) {
  final unknown = json.keys.where((key) => !keys.contains(key)).toList();
  final missing = keys.where((key) => !json.containsKey(key)).toList();
  if (unknown.isNotEmpty || missing.isNotEmpty) {
    throw FormatException(
      '$field keys do not match schema; unknown=$unknown missing=$missing.',
    );
  }
}

String _text(Map<String, Object?> json, String key, int maxLength) {
  final value = json[key];
  if (value is! String ||
      value.isEmpty ||
      value.length > maxLength ||
      utf8.encode(value).length > maxLength ||
      value.contains('\u0000')) {
    throw FormatException('$key must be bounded non-empty text.');
  }
  return value;
}

String? _nullableText(Map<String, Object?> json, String key, int maxLength) {
  final value = json[key];
  if (value == null) return null;
  if (value is! String ||
      value.length > maxLength ||
      utf8.encode(value).length > maxLength ||
      value.contains('\u0000')) {
    throw FormatException('$key must be bounded text or null.');
  }
  return value;
}

int _integer(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! int) throw FormatException('$key must be an integer.');
  return value;
}

int _positiveInteger(Map<String, Object?> json, String key) {
  final value = _integer(json, key);
  if (value <= 0) throw FormatException('$key must be positive.');
  return value;
}

bool _bool(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! bool) throw FormatException('$key must be a boolean.');
  return value;
}

bool? _nullableBool(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value != null && value is! bool) {
    throw FormatException('$key must be a boolean or null.');
  }
  return value as bool?;
}

List<Object?> _list(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! List) throw FormatException('$key must be an array.');
  return value.cast<Object?>();
}

T _enumValue<T>(
  Map<String, Object?> json,
  String key,
  List<T> values,
  String Function(T value) name,
) {
  final raw = _text(json, key, 64);
  for (final value in values) {
    if (name(value) == raw) return value;
  }
  throw FormatException('$key contains an unknown value.');
}

String _digest(Map<String, Object?> json, String key) {
  final value = _text(json, key, 64);
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw FormatException('$key must be a lowercase SHA-256 digest.');
  }
  return value;
}

String? _nullableDigest(Map<String, Object?> json, String key) {
  if (json[key] == null) return null;
  return _digest(json, key);
}
