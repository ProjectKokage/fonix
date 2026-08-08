import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:analyzer/dart/analysis/analysis_context_collection.dart';
import 'package:analyzer/dart/analysis/results.dart';
import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/visitor.dart';
import 'package:analyzer/dart/constant/value.dart';
import 'package:analyzer/dart/element/element.dart';
import 'package:analyzer/dart/element/nullability_suffix.dart';
import 'package:analyzer/dart/element/type.dart';
import 'package:analyzer/diagnostic/diagnostic.dart';
import 'package:crypto/crypto.dart';

const String publicDartApiBaselinePath = 'release/public-dart-api-v1.json';
const String publicDartApiLibraryUri = 'package:fonix/fonix.dart';
const String publicDartApiDartSdkVersion = '3.11.5';
const String publicDartApiAnalyzerVersion = '14.1.0';
const int publicDartApiMaximumBaselineBytes = 16 * 1024 * 1024;
const String _claimBoundary =
    'This record freezes the resolved public Dart declaration surface for '
    'review. It does not classify compatibility, approve the native ABI, '
    'prove target behavior, or replace external API/ABI approval.';

final RegExp _packageName = RegExp(
  r'^name: ([a-z][a-z0-9_]*)$',
  multiLine: true,
);
final RegExp _packageVersion = RegExp(
  r'^version: ([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$',
  multiLine: true,
);
final RegExp _minimumSdk = RegExp(
  r'^  sdk: \^([0-9]+\.[0-9]+\.[0-9]+)$',
  multiLine: true,
);

final class PublicDartApiError implements Exception {
  const PublicDartApiError(this.message);

  final String message;

  @override
  String toString() => message;
}

String _oneMatch(RegExp expression, String source, String label) {
  final List<RegExpMatch> matches = expression.allMatches(source).toList();
  if (matches.length != 1) {
    throw PublicDartApiError('$label must have exactly one canonical value.');
  }
  return matches.single.group(1)!;
}

String _readBoundedText(
  File file,
  String label, {
  int maximumBytes = publicDartApiMaximumBaselineBytes,
}) {
  if (FileSystemEntity.typeSync(file.path, followLinks: false) !=
      FileSystemEntityType.file) {
    throw PublicDartApiError('$label must be a regular file, not a link.');
  }
  final FileStat before = file.statSync();
  if (before.size < 0 || before.size > maximumBytes) {
    throw PublicDartApiError('$label is outside its byte bound.');
  }
  final List<int> bytes = file.readAsBytesSync();
  final FileStat after = file.statSync();
  if (bytes.length != before.size ||
      after.size != before.size ||
      after.modified != before.modified ||
      after.type != FileSystemEntityType.file) {
    throw PublicDartApiError('$label changed while it was read.');
  }
  try {
    return utf8.decode(bytes, allowMalformed: false);
  } on FormatException {
    throw PublicDartApiError('$label must be strict UTF-8.');
  }
}

const int _maximumLibEntries = 16384;
const int _maximumDartSourceFiles = 4096;

Future<List<String>> _boundedDartSourcePaths(Directory repository) async {
  final Directory lib = Directory('${repository.path}/lib');
  if (FileSystemEntity.typeSync(lib.path, followLinks: false) !=
      FileSystemEntityType.directory) {
    throw const PublicDartApiError('lib must be a directory, not a link.');
  }
  var entryCount = 0;
  final List<String> dartSources = <String>[];
  try {
    await for (final FileSystemEntity entity in lib.list(
      recursive: true,
      followLinks: false,
    )) {
      entryCount += 1;
      if (entryCount > _maximumLibEntries) {
        throw const PublicDartApiError(
          'lib exceeds its filesystem-entry count bound.',
        );
      }
      final FileSystemEntityType type = FileSystemEntity.typeSync(
        entity.path,
        followLinks: false,
      );
      if (type == FileSystemEntityType.link) {
        throw PublicDartApiError(
          'lib must not contain filesystem links: ${entity.path}.',
        );
      }
      if (!entity.path.endsWith('.dart')) {
        continue;
      }
      if (type != FileSystemEntityType.file) {
        throw PublicDartApiError(
          'Dart source must be a regular file: ${entity.path}.',
        );
      }
      dartSources.add(entity.path);
      if (dartSources.length > _maximumDartSourceFiles) {
        throw const PublicDartApiError(
          'lib exceeds its Dart source-file count bound.',
        );
      }
    }
  } on FileSystemException catch (error) {
    throw PublicDartApiError(
      'lib could not be enumerated safely: ${error.message}.',
    );
  }
  dartSources.sort();
  return dartSources;
}

final class _PublicDartApiSourceVisitor extends RecursiveAstVisitor<void> {
  String? failure;

  void _fail(String message) {
    failure ??= message;
  }

  void _checkEnvironmentElement(Element? element) {
    if (element?.library?.uri.toString() != 'dart:core') {
      return;
    }
    final String? ownerName = element?.enclosingElement?.name;
    final String? memberName = element?.name ?? element?.lookupName;
    final bool isFromEnvironment =
        memberName == 'fromEnvironment' &&
        (ownerName == 'bool' || ownerName == 'int' || ownerName == 'String');
    final bool isHasEnvironment =
        memberName == 'hasEnvironment' && ownerName == 'bool';
    if (isFromEnvironment || isHasEnvironment) {
      _fail(
        'The dart:core $ownerName.$memberName environment-dependent '
        'constant invocation is not allowed.',
      );
    }
  }

  bool _hasFunctionTypeMetadata(
    FormalParameterList parameters,
    TypeParameterList? typeParameters,
  ) {
    return parameters.parameters.any(
          (FormalParameter parameter) => parameter.metadata.isNotEmpty,
        ) ||
        (typeParameters?.typeParameters.any(
              (TypeParameter parameter) => parameter.metadata.isNotEmpty,
            ) ??
            false);
  }

  void _rejectFunctionTypeMetadata(
    FormalParameterList parameters,
    TypeParameterList? typeParameters,
  ) {
    if (_hasFunctionTypeMetadata(parameters, typeParameters)) {
      _fail(
        'Function-type formal/type-parameter metadata cannot be represented '
        'canonically by the pinned analyzer and is rejected.',
      );
    }
  }

  @override
  void visitDotShorthandConstructorInvocation(
    DotShorthandConstructorInvocation node,
  ) {
    _checkEnvironmentElement(node.element);
    super.visitDotShorthandConstructorInvocation(node);
  }

  @override
  void visitDotShorthandInvocation(DotShorthandInvocation node) {
    _checkEnvironmentElement(node.memberName.element);
    super.visitDotShorthandInvocation(node);
  }

  @override
  void visitExportDirective(ExportDirective node) {
    if (node.configurations.isNotEmpty) {
      _fail('Conditional import/export configurations are not allowed.');
    }
    super.visitExportDirective(node);
  }

  @override
  void visitFunctionTypeAlias(FunctionTypeAlias node) {
    _rejectFunctionTypeMetadata(node.parameters, node.typeParameters);
    super.visitFunctionTypeAlias(node);
  }

  @override
  void visitFunctionTypedFormalParameterSuffix(
    FunctionTypedFormalParameterSuffix node,
  ) {
    _rejectFunctionTypeMetadata(node.formalParameters, node.typeParameters);
    super.visitFunctionTypedFormalParameterSuffix(node);
  }

  @override
  void visitGenericFunctionType(GenericFunctionType node) {
    _rejectFunctionTypeMetadata(node.parameters, node.typeParameters);
    super.visitGenericFunctionType(node);
  }

  @override
  void visitImportDirective(ImportDirective node) {
    if (node.configurations.isNotEmpty) {
      _fail('Conditional import/export configurations are not allowed.');
    }
    super.visitImportDirective(node);
  }

  @override
  void visitInstanceCreationExpression(InstanceCreationExpression node) {
    _checkEnvironmentElement(node.constructorName.element);
    super.visitInstanceCreationExpression(node);
  }

  @override
  void visitMethodInvocation(MethodInvocation node) {
    _checkEnvironmentElement(node.methodName.element);
    super.visitMethodInvocation(node);
  }
}

Map<String, String> _packageIdentity(Directory repository) {
  final String pubspec = _readBoundedText(
    File('${repository.path}/pubspec.yaml'),
    'pubspec.yaml',
  );
  if (!RegExp(r'^  analyzer: 14\.1\.0$', multiLine: true).hasMatch(pubspec)) {
    throw const PublicDartApiError(
      'pubspec.yaml must pin analyzer 14.1.0 as a direct dev dependency.',
    );
  }
  final String lock = _readBoundedText(
    File('${repository.path}/pubspec.lock'),
    'pubspec.lock',
  );
  final RegExp lockedAnalyzer = RegExp(
    r'^  analyzer:\n'
    r'    dependency: "direct dev"\n'
    r'    description:\n'
    r'(?:      [^\n]+\n)+'
    r'    source: hosted\n'
    r'    version: "14\.1\.0"$',
    multiLine: true,
  );
  if (!lockedAnalyzer.hasMatch(lock)) {
    throw const PublicDartApiError(
      'pubspec.lock must resolve analyzer 14.1.0 as a direct dev dependency.',
    );
  }
  return <String, String>{
    'name': _oneMatch(_packageName, pubspec, 'package name'),
    'version': _oneMatch(_packageVersion, pubspec, 'package version'),
    'minimumSdk': _oneMatch(_minimumSdk, pubspec, 'minimum Dart SDK'),
  };
}

Map<String, Object?> _elementIdentity(Element element) {
  final LibraryElement? library = element.library;
  if (library == null) {
    throw PublicDartApiError(
      'Cannot identify ${element.displayName} without an owning library.',
    );
  }
  final Element? enclosing = element.enclosingElement;
  return <String, Object?>{
    'libraryUri': library.uri.toString(),
    'kind': element.kind.name,
    'name': element.lookupName ?? element.displayName,
    if (enclosing != null && enclosing is! LibraryElement)
      'enclosing': <String, Object?>{
        'kind': enclosing.kind.name,
        'name': enclosing.lookupName ?? enclosing.displayName,
      },
  };
}

final class _TypeIdentityEncoder {
  static const int _maximumDepth = 32;
  static const int _maximumNodes = 1024;

  int _nodes = 0;

  Map<String, Object?> encode(
    DartType type, [
    int depth = 0,
    TypeAliasElement? suppressedAlias,
  ]) {
    _nodes += 1;
    if (_nodes > _maximumNodes || depth > _maximumDepth) {
      throw const PublicDartApiError(
        'Public Dart API type exceeds the canonical identity bound.',
      );
    }
    final alias = type.alias;
    if (alias != null && alias.element != suppressedAlias) {
      final DartType instantiatedTarget = alias.element.instantiate(
        typeArguments: alias.typeArguments,
        nullabilitySuffix: alias.nullabilitySuffix,
      );
      return <String, Object?>{
        'kind': 'alias',
        'element': _elementIdentity(alias.element),
        'arguments': <Object?>[
          for (final DartType argument in alias.typeArguments)
            encode(argument, depth + 1),
        ],
        'declaredTarget': encode(
          alias.element.aliasedType,
          depth + 1,
          alias.element,
        ),
        'instantiatedTarget': encode(
          instantiatedTarget,
          depth + 1,
          alias.element,
        ),
        'nullability': _nullabilityName(alias.nullabilitySuffix),
        'signature': type.getDisplayString(),
      };
    }
    if (type is DynamicType) {
      return <String, Object?>{'kind': 'dynamic'};
    }
    if (type is VoidType) {
      return <String, Object?>{'kind': 'void'};
    }
    if (type is NeverType) {
      return <String, Object?>{
        'kind': 'never',
        'nullability': _nullabilityName(type.nullabilitySuffix),
      };
    }
    if (type is InterfaceType) {
      return <String, Object?>{
        'kind': 'interface',
        'element': _elementIdentity(type.element),
        'arguments': <Object?>[
          for (final DartType argument in type.typeArguments)
            encode(argument, depth + 1),
        ],
        if (type.element is ExtensionTypeElement)
          'extensionTypeErasure': encode(type.extensionTypeErasure, depth + 1),
        'nullability': _nullabilityName(type.nullabilitySuffix),
        'signature': type.getDisplayString(),
      };
    }
    if (type is FunctionType) {
      return <String, Object?>{
        'kind': 'function',
        'returnType': encode(type.returnType, depth + 1),
        'parameters': <Object?>[
          for (final FormalParameterElement parameter in type.formalParameters)
            <String, Object?>{
              'kind': _formalParameterKind(parameter),
              'name': parameter.name,
              'type': encode(parameter.type, depth + 1),
            },
        ],
        'typeParameters': <Object?>[
          for (final TypeParameterElement parameter in type.typeParameters)
            <String, Object?>{
              'identity': _elementIdentity(parameter),
              if (parameter.bound != null)
                'bound': encode(parameter.bound!, depth + 1),
            },
        ],
        'nullability': _nullabilityName(type.nullabilitySuffix),
        'signature': type.getDisplayString(),
      };
    }
    if (type is RecordType) {
      final List<RecordTypeNamedField> named = type.namedFields.toList()
        ..sort(
          (RecordTypeNamedField left, RecordTypeNamedField right) =>
              left.name.compareTo(right.name),
        );
      return <String, Object?>{
        'kind': 'record',
        'positional': <Object?>[
          for (final RecordTypePositionalField field in type.positionalFields)
            encode(field.type, depth + 1),
        ],
        'named': <Object?>[
          for (final RecordTypeNamedField field in named)
            <String, Object?>{
              'name': field.name,
              'type': encode(field.type, depth + 1),
            },
        ],
        'nullability': _nullabilityName(type.nullabilitySuffix),
        'signature': type.getDisplayString(),
      };
    }
    if (type is TypeParameterType) {
      return <String, Object?>{
        'kind': 'typeParameter',
        'element': _elementIdentity(type.element),
        'nullability': _nullabilityName(type.nullabilitySuffix),
      };
    }
    if (type is InvalidType) {
      throw const PublicDartApiError(
        'Invalid type cannot enter the public Dart API baseline.',
      );
    }
    throw PublicDartApiError(
      'Unsupported public Dart API type structure: ${type.runtimeType}.',
    );
  }
}

String _nullabilityName(NullabilitySuffix suffix) => switch (suffix) {
  NullabilitySuffix.none => 'none',
  NullabilitySuffix.question => 'question',
  NullabilitySuffix.star => 'star',
};

String _formalParameterKind(FormalParameterElement parameter) {
  if (parameter.isRequiredPositional) {
    return 'requiredPositional';
  }
  if (parameter.isOptionalPositional) {
    return 'optionalPositional';
  }
  if (parameter.isRequiredNamed) {
    return 'requiredNamed';
  }
  if (parameter.isOptionalNamed) {
    return 'optionalNamed';
  }
  throw PublicDartApiError(
    'Unsupported formal parameter kind: ${parameter.displayName}.',
  );
}

Map<String, Object?> _typeIdentity(DartType type) =>
    _TypeIdentityEncoder().encode(type);

Map<String, Object?> _constructorTargetIdentity(
  ConstructorElement constructor, {
  bool rejectParameterDefaults = false,
}) => _ConstructorTargetIdentityEncoder(
  rejectParameterDefaults: rejectParameterDefaults,
).encode(constructor);

final class _ConstructorTargetIdentityEncoder {
  _ConstructorTargetIdentityEncoder({required this.rejectParameterDefaults});

  static const int _maximumDepth = 32;
  static const int _maximumNodes = 128;

  final bool rejectParameterDefaults;
  final Set<String> _active = <String>{};
  int _nodes = 0;

  Map<String, Object?> encode(ConstructorElement constructor, [int depth = 0]) {
    _nodes += 1;
    if (_nodes > _maximumNodes || depth > _maximumDepth) {
      throw const PublicDartApiError(
        'Redirecting constructor chain exceeds its canonical identity bound.',
      );
    }
    if (rejectParameterDefaults &&
        constructor.formalParameters.any(
          (FormalParameterElement parameter) => parameter.hasDefaultValue,
        )) {
      throw const PublicDartApiError(
        'Constructor tear-off constants with explicit parameter defaults '
        'cannot be represented canonically by the pinned analyzer and are '
        'rejected.',
      );
    }
    final Map<String, Object?> record = <String, Object?>{
      ..._elementIdentity(constructor),
      'signature': constructor.displayString(preferTypeAlias: true),
      'returnType': _typeIdentity(constructor.returnType),
      'parameters': <Object?>[
        for (final FormalParameterElement parameter
            in constructor.formalParameters)
          <String, Object?>{
            'kind': _formalParameterKind(parameter),
            'name': parameter.name,
            'type': _typeIdentity(parameter.type),
          },
      ],
    };
    final String key = jsonEncode(_canonicalJsonValue(record));
    if (!_active.add(key)) {
      throw const PublicDartApiError(
        'Redirecting constructor chain contains a cycle.',
      );
    }
    try {
      if (constructor.redirectedConstructor case final redirectTarget?) {
        record['redirectTarget'] = encode(redirectTarget, depth + 1);
      }
      return record;
    } finally {
      _active.remove(key);
    }
  }
}

DartType? _nonErasedConstantType(DartObject value) {
  // Analyzer 14.1 exposes the declared extension-type identity only here.
  // This gate is pinned to that exact analyzer and Dart SDK combination.
  // ignore: experimental_member_use
  return value.typeNotExtensionTypeErased ?? value.type;
}

DartType? _nonErasedTypeValue(DartObject value) {
  // See the pinned experimental API rationale above.
  // ignore: experimental_member_use
  return value.toTypeValueNotExtensionTypeErased() ?? value.toTypeValue();
}

String _doubleBitPattern(double value) {
  final ByteData data = ByteData(8)..setFloat64(0, value, Endian.big);
  return data.buffer.asUint8List().map((int byte) {
    return byte.toRadixString(16).padLeft(2, '0');
  }).join();
}

final class _ConstantValueEncoder {
  static const int _maximumDepth = 32;
  static const int _maximumNodes = 4096;

  int _nodes = 0;

  List<Map<String, Object?>> _evaluatedInstanceFields(
    DartObject value,
    InterfaceType type,
    int depth,
  ) {
    final List<InterfaceType> hierarchy = <InterfaceType>[
      type,
      ...type.allSupertypes,
    ];
    if (hierarchy.length > _maximumDepth) {
      throw const PublicDartApiError(
        'Constructed annotation interface hierarchy exceeds its bound.',
      );
    }
    final Map<InterfaceElement, DartObject> stateOwners =
        <InterfaceElement, DartObject>{type.element: value};
    var currentType = type;
    var currentValue = value;
    while (true) {
      final InterfaceType? superclass = currentType.superclass;
      if (superclass == null) {
        break;
      }
      if (stateOwners.containsKey(superclass.element)) {
        break;
      }
      final DartObject? superclassValue = currentValue.getField('(super)');
      if (superclassValue == null) {
        break;
      }
      stateOwners[superclass.element] = superclassValue;
      currentType = superclass;
      currentValue = superclassValue;
      if (stateOwners.length > _maximumDepth) {
        throw const PublicDartApiError(
          'Constructed annotation superclass state exceeds its bound.',
        );
      }
    }

    final Map<String, FieldElement> fields = <String, FieldElement>{};
    for (final InterfaceType interface in hierarchy) {
      for (final FieldElement field in interface.element.fields) {
        if (field.isStatic || !field.isOriginDeclaration || field.isAbstract) {
          continue;
        }
        final String key = jsonEncode(
          _canonicalJsonValue(_elementIdentity(field)),
        );
        fields.putIfAbsent(key, () => field);
      }
    }
    if (fields.length > _maximumNodes) {
      throw const PublicDartApiError(
        'Constructed annotation field inventory exceeds its bound.',
      );
    }
    final List<String> keys = fields.keys.toList()..sort();
    final List<Map<String, Object?>> result = <Map<String, Object?>>[];
    for (final String key in keys) {
      final FieldElement field = fields[key]!;
      final String? fieldName = field.name;
      if (fieldName == null) {
        throw const PublicDartApiError(
          'Constructed annotation contains an unnamed instance field.',
        );
      }
      final DartObject? owner = stateOwners[field.enclosingElement];
      final DartObject? fieldValue =
          owner?.getField(fieldName) ?? value.getField(fieldName);
      if (fieldValue == null) {
        throw PublicDartApiError(
          'Constructed annotation field could not be evaluated: '
          '${field.library.uri} ${field.enclosingElement.name}.$fieldName.',
        );
      }
      result.add(<String, Object?>{
        'identity': _elementIdentity(field),
        'value': encode(fieldValue, depth + 1),
      });
    }
    return result;
  }

  Object? encode(DartObject value, [int depth = 0]) {
    _nodes += 1;
    if (_nodes > _maximumNodes || depth > _maximumDepth) {
      throw const PublicDartApiError(
        'Public annotation constant exceeds the canonical value bound.',
      );
    }
    if (value.isNull) {
      return <String, Object?>{'kind': 'null'};
    }
    final DartType? erasedType = value.type;
    final DartType? type = _nonErasedConstantType(value);
    final Map<String, Object?>? typeIdentity = type == null
        ? null
        : _typeIdentity(type);
    final Map<String, Object?>? erasedTypeIdentity = erasedType == null
        ? null
        : _typeIdentity(erasedType);
    final Map<String, Object?> typeRecords = <String, Object?>{
      'type': ?typeIdentity,
      'erasedType': ?erasedTypeIdentity,
    };
    final String? typeSignature = type?.getDisplayString();
    final VariableElement? variable = value.variable;
    if (erasedType?.isDartCoreBool ?? false) {
      final bool? scalar = value.toBoolValue();
      if (scalar == null) {
        throw const PublicDartApiError(
          'Unknown bool constant cannot enter the public Dart API baseline.',
        );
      }
      return <String, Object?>{'kind': 'bool', ...typeRecords, 'value': scalar};
    }
    if (erasedType?.isDartCoreInt ?? false) {
      final int? scalar = value.toIntValue();
      if (scalar == null) {
        throw const PublicDartApiError(
          'Unknown int constant cannot enter the public Dart API baseline.',
        );
      }
      return <String, Object?>{'kind': 'int', ...typeRecords, 'value': scalar};
    }
    if (erasedType?.isDartCoreDouble ?? false) {
      final double? scalar = value.toDoubleValue();
      if (scalar == null) {
        throw const PublicDartApiError(
          'Unknown double constant cannot enter the public Dart API baseline.',
        );
      }
      return <String, Object?>{
        'kind': 'double',
        ...typeRecords,
        'value': scalar.toString(),
        'bits': _doubleBitPattern(scalar),
      };
    }
    if (erasedType?.isDartCoreString ?? false) {
      final String? scalar = value.toStringValue();
      if (scalar == null) {
        throw const PublicDartApiError(
          'Unknown String constant cannot enter the public Dart API baseline.',
        );
      }
      return <String, Object?>{
        'kind': 'string',
        ...typeRecords,
        'value': scalar,
      };
    }
    final String? symbol = value.toSymbolValue();
    if (symbol != null) {
      if (symbol.startsWith('_')) {
        throw const PublicDartApiError(
          'Private Symbol constants cannot enter the public Dart API baseline.',
        );
      }
      return <String, Object?>{
        'kind': 'symbol',
        ...typeRecords,
        'value': symbol,
        if (variable != null) 'reference': _elementIdentity(variable),
      };
    }
    final DartType? erasedTypeValue = value.toTypeValue();
    final DartType? typeValue = _nonErasedTypeValue(value);
    if (typeValue != null) {
      if (erasedTypeValue == null) {
        throw const PublicDartApiError(
          'Type constant has no canonical erased type identity.',
        );
      }
      return <String, Object?>{
        'kind': 'type',
        ...typeRecords,
        'value': _typeIdentity(typeValue),
        'erasedValue': _typeIdentity(erasedTypeValue),
      };
    }
    final ExecutableElement? function = value.toFunctionValue();
    if (function != null) {
      if (function is ConstructorElement) {
        return <String, Object?>{
          'kind': 'function',
          ...typeRecords,
          'value': _constructorTargetIdentity(
            function,
            rejectParameterDefaults: true,
          ),
        };
      }
      if (function.typeParameters.isNotEmpty) {
        throw const PublicDartApiError(
          'Generic function tear-off constants cannot be represented '
          'canonically by the pinned analyzer and are rejected.',
        );
      }
      return <String, Object?>{
        'kind': 'function',
        ...typeRecords,
        'value': _elementIdentity(function),
      };
    }
    final List<DartObject>? list = value.toListValue();
    if (list != null) {
      return <String, Object?>{
        'kind': 'list',
        ...typeRecords,
        'values': <Object?>[
          for (final DartObject item in list) encode(item, depth + 1),
        ],
      };
    }
    final Set<DartObject>? set = value.toSetValue();
    if (set != null) {
      return <String, Object?>{
        'kind': 'set',
        ...typeRecords,
        'values': <Object?>[
          for (final DartObject item in set) encode(item, depth + 1),
        ],
      };
    }
    final Map<DartObject?, DartObject?>? map = value.toMapValue();
    if (map != null) {
      return <String, Object?>{
        'kind': 'map',
        ...typeRecords,
        'entries': <Map<String, Object?>>[
          for (final MapEntry<DartObject?, DartObject?> entry in map.entries)
            <String, Object?>{
              'key': entry.key == null
                  ? <String, Object?>{'kind': 'null'}
                  : encode(entry.key!, depth + 1),
              'value': entry.value == null
                  ? <String, Object?>{'kind': 'null'}
                  : encode(entry.value!, depth + 1),
            },
        ],
      };
    }
    final record = value.toRecordValue();
    if (record != null) {
      return <String, Object?>{
        'kind': 'record',
        ...typeRecords,
        'positional': <Object?>[
          for (final DartObject item in record.positional)
            encode(item, depth + 1),
        ],
        'named': <String, Object?>{
          for (final MapEntry<String, DartObject> entry in record.named.entries)
            entry.key: encode(entry.value, depth + 1),
        },
      };
    }
    final ConstructorInvocation? invocation = value.constructorInvocation;
    if (type is InterfaceType && type.element is EnumElement) {
      final EnumElement owner = type.element as EnumElement;
      final int ordinal = owner.constants.indexWhere((FieldElement candidate) {
        if (variable is FieldElement && variable.isEnumConstant) {
          return candidate.baseElement == variable.baseElement;
        }
        return candidate.computeConstantValue() == value;
      });
      if (ordinal < 0) {
        throw const PublicDartApiError(
          'Enum annotation constant has no canonical ordinal.',
        );
      }
      final FieldElement enumConstant = owner.constants[ordinal];
      return <String, Object?>{
        'kind': 'enum',
        ...typeRecords,
        'value': _elementIdentity(enumConstant),
        'ordinal': ordinal,
        'fields': _evaluatedInstanceFields(value, type, depth),
        if (variable != null && variable != enumConstant)
          'reference': _elementIdentity(variable),
      };
    }
    if (invocation != null) {
      if (type is! InterfaceType) {
        throw const PublicDartApiError(
          'Constructed annotation constant has no interface type identity.',
        );
      }
      return <String, Object?>{
        'kind': 'constructed',
        ...typeRecords,
        'constructor': _constructorTargetIdentity(invocation.constructor),
        'fields': _evaluatedInstanceFields(value, type, depth),
        'positionalArguments': <Object?>[
          for (final DartObject argument in invocation.positionalArguments)
            encode(argument, depth + 1),
        ],
        'namedArguments': <String, Object?>{
          for (final MapEntry<String, DartObject> argument
              in invocation.namedArguments.entries)
            argument.key: encode(argument.value, depth + 1),
        },
        if (variable != null) 'reference': _elementIdentity(variable),
      };
    }
    if (value.hasKnownValue) {
      throw PublicDartApiError(
        'Unsupported known public annotation constant kind: '
        '${typeSignature ?? '<no type>'}.',
      );
    }
    throw PublicDartApiError(
      'Unknown public API constant cannot be encoded canonically: '
      '${typeSignature ?? '<no type>'}.',
    );
  }
}

Object? encodePublicDartApiConstantForTesting(DartObject value) =>
    _ConstantValueEncoder().encode(value);

List<Map<String, Object?>> _annotationRecords(Metadata metadata) {
  return <Map<String, Object?>>[
    for (final ElementAnnotation annotation in metadata.annotations)
      _annotationRecord(annotation),
  ];
}

Map<String, Object?> _annotationRecord(ElementAnnotation annotation) {
  final DartObject? value = annotation.computeConstantValue();
  final List<Diagnostic>? errors = annotation.constantEvaluationErrors;
  if (value == null || errors == null || errors.isNotEmpty) {
    throw PublicDartApiError(
      'Public annotation could not be evaluated canonically: '
      '${annotation.toSource()}.',
    );
  }
  final Element? element = annotation.element;
  if (element == null) {
    throw PublicDartApiError(
      'Public annotation has no resolved identity: ${annotation.toSource()}.',
    );
  }
  return <String, Object?>{
    'identity': _elementIdentity(element),
    'source': annotation.toSource(),
    'value': _ConstantValueEncoder().encode(value),
  };
}

void _addAnnotations(Map<String, Object?> record, Metadata metadata) {
  final List<Map<String, Object?>> annotations = _annotationRecords(metadata);
  if (annotations.isNotEmpty) {
    record['annotations'] = annotations;
  }
}

List<Map<String, Object?>> _formalParameterAnnotations(
  ExecutableElement executable,
) {
  final List<Map<String, Object?>> result = <Map<String, Object?>>[];
  for (var index = 0; index < executable.formalParameters.length; index += 1) {
    final FormalParameterElement parameter = executable.formalParameters[index];
    final List<Map<String, Object?>> annotations = _annotationRecords(
      parameter.metadata,
    );
    if (annotations.isNotEmpty) {
      result.add(<String, Object?>{
        'index': index,
        'name': parameter.name,
        'annotations': annotations,
      });
    }
  }
  return result;
}

List<Map<String, Object?>> _typeParameterAnnotations(
  TypeParameterizedElement declaration,
) {
  final List<Map<String, Object?>> result = <Map<String, Object?>>[];
  for (var index = 0; index < declaration.typeParameters.length; index += 1) {
    final TypeParameterElement parameter = declaration.typeParameters[index];
    final List<Map<String, Object?>> annotations = _annotationRecords(
      parameter.metadata,
    );
    if (annotations.isNotEmpty) {
      result.add(<String, Object?>{
        'index': index,
        'name': parameter.name,
        'annotations': annotations,
      });
    }
  }
  return result;
}

Object? _evaluatedVariableValue(VariableElement variable, String label) {
  final DartObject? value = variable.computeConstantValue();
  if (value == null) {
    throw PublicDartApiError('$label could not be evaluated canonically.');
  }
  return _ConstantValueEncoder().encode(value);
}

Map<String, Object?> _formalParameterRecord(
  FormalParameterElement parameter,
  int index,
) {
  final Map<String, Object?> record = <String, Object?>{
    'index': index,
    'identity': _elementIdentity(parameter),
    'name': parameter.name,
    'kind': _formalParameterKind(parameter),
    'covariant': parameter.isCovariant,
    'implicitType': parameter.hasImplicitType,
    'type': _typeIdentity(parameter.type),
  };
  if (parameter.hasDefaultValue) {
    final String? source = parameter.defaultValueCode;
    if (source == null) {
      throw PublicDartApiError(
        'Default value source is unavailable for public parameter '
        '${parameter.displayName}.',
      );
    }
    record['defaultValue'] = <String, Object?>{
      'explicit': true,
      'source': source,
      'value': _evaluatedVariableValue(
        parameter,
        'Default value for public parameter ${parameter.displayName}',
      ),
    };
  } else if (parameter.isOptional) {
    record['defaultValue'] = <String, Object?>{
      'explicit': false,
      'value': <String, Object?>{'kind': 'null'},
    };
  }
  return record;
}

List<Map<String, Object?>> _typeParameterRecords(
  TypeParameterizedElement declaration,
) {
  return <Map<String, Object?>>[
    for (var index = 0; index < declaration.typeParameters.length; index += 1)
      <String, Object?>{
        'index': index,
        'identity': _elementIdentity(declaration.typeParameters[index]),
        'name': declaration.typeParameters[index].name,
        if (declaration.typeParameters[index].bound case final DartType bound)
          'bound': _typeIdentity(bound),
      },
  ];
}

Map<String, Object?> _variableRecord(VariableElement variable) {
  final bool hasConstantValue =
      variable.isConst || variable is FieldElement && variable.isEnumConstant;
  final Map<String, Object?> record = <String, Object?>{
    'identity': _elementIdentity(variable),
    'type': _typeIdentity(variable.type),
    'const': variable.isConst,
    'final': variable.isFinal,
    'late': variable.isLate,
    'static': variable.isStatic,
    'implicitType': variable.hasImplicitType,
    if (variable is PropertyInducingElement) ...<String, Object?>{
      'hasInitializer': variable.hasInitializer,
      'originDeclaration': variable.isOriginDeclaration,
      'originGetterSetter': variable.isOriginGetterSetter,
    },
    if (hasConstantValue)
      'constantInitializer': variable.constantInitializer?.toSource(),
    if (hasConstantValue)
      'constantValue': _evaluatedVariableValue(
        variable,
        'Public constant ${variable.displayName}',
      ),
  };
  _addAnnotations(record, variable.metadata);
  return record;
}

Map<String, Object?> _elementRecord(Element element, {String? exportedName}) {
  if (element is MultiplyDefinedElement || element.library == null) {
    throw PublicDartApiError(
      'Public name ${exportedName ?? element.displayName} is multiply defined.',
    );
  }
  final Map<String, Object?> record = <String, Object?>{
    'exportedName': ?exportedName,
    'identity': _elementIdentity(element),
    'kind': element.kind.name,
    'name': element.lookupName ?? element.displayName,
    'signature': element.displayString(preferTypeAlias: true),
    'deprecated': element.metadata.hasDeprecated,
  };
  _addAnnotations(record, element.metadata);
  if (element is ExecutableElement) {
    record['executable'] = <String, Object?>{
      'abstract': element.isAbstract,
      'external': element.isExternal,
      'static': element.isStatic,
      'returnType': _typeIdentity(element.returnType),
      'parameters': <Map<String, Object?>>[
        for (var index = 0; index < element.formalParameters.length; index += 1)
          _formalParameterRecord(element.formalParameters[index], index),
      ],
    };
    final List<Map<String, Object?>> parameterAnnotations =
        _formalParameterAnnotations(element);
    if (parameterAnnotations.isNotEmpty) {
      record['parameterAnnotations'] = parameterAnnotations;
    }
  }
  if (element is ConstructorElement) {
    final ConstructorElement? redirectTarget = element.redirectedConstructor;
    record['constructor'] = <String, Object?>{
      'const': element.isConst,
      'factory': element.isFactory,
      if (redirectTarget != null)
        'redirectTarget': _constructorTargetIdentity(redirectTarget),
    };
  }
  if (element is TypeParameterizedElement) {
    record['typeParameters'] = _typeParameterRecords(element);
    final List<Map<String, Object?>> parameterAnnotations =
        _typeParameterAnnotations(element);
    if (parameterAnnotations.isNotEmpty) {
      record['typeParameterAnnotations'] = parameterAnnotations;
    }
  }
  if (element is PropertyAccessorElement) {
    record['property'] = <String, Object?>{
      'originDeclaration': element.isOriginDeclaration,
      'originInterface': element.isOriginInterface,
      'originVariable': element.isOriginVariable,
      'variable': _variableRecord(element.variable),
    };
  } else if (element is VariableElement) {
    record['variable'] = _variableRecord(element);
  }
  if (element is FieldElement) {
    record['field'] = <String, Object?>{
      'abstract': element.isAbstract,
      'covariant': element.isCovariant,
      'enumConstant': element.isEnumConstant,
      'external': element.isExternal,
    };
  }
  if (element is InterfaceElement) {
    record['interfaceTypes'] = <String, Object?>{
      if (element.supertype case final InterfaceType supertype)
        'superclass': _typeIdentity(supertype),
      'interfaces': <Object?>[
        for (final InterfaceType interface in element.interfaces)
          _typeIdentity(interface),
      ],
      'mixins': <Object?>[
        for (final InterfaceType mixin in element.mixins) _typeIdentity(mixin),
      ],
      if (element is MixinElement)
        'onConstraints': <Object?>[
          for (final InterfaceType constraint in element.superclassConstraints)
            _typeIdentity(constraint),
        ],
    };
  }
  if (element is TypeAliasElement) {
    record['aliasedType'] = _typeIdentity(element.aliasedType);
  }
  if (element is ExtensionElement) {
    record['extendedType'] = _typeIdentity(element.extendedType);
  }
  if (element is ExtensionTypeElement) {
    record['representation'] = <String, Object?>{
      'identity': _elementIdentity(element.representation),
      'type': _typeIdentity(element.representation.type),
    };
    record['typeErasure'] = _typeIdentity(element.typeErasure);
  }
  return record;
}

String _recordSortKey(Map<String, Object?> record) =>
    '${record['name']}\u0000${record['kind']}\u0000${record['signature']}';

List<Map<String, Object?>> _sortedRecords(Iterable<Element> elements) {
  final List<Map<String, Object?>> records = elements
      .where((Element element) => element.isPublic)
      .map((Element element) => _elementRecord(element))
      .toList();
  records.sort(
    (Map<String, Object?> left, Map<String, Object?> right) =>
        _recordSortKey(left).compareTo(_recordSortKey(right)),
  );
  return records;
}

Map<String, Object?> _exportedElementRecord(
  String exportedName,
  Element element,
) {
  if (exportedName.startsWith('_') || !element.isPublic) {
    throw PublicDartApiError(
      'The export namespace contains a private name: $exportedName.',
    );
  }
  final Map<String, Object?> record = _elementRecord(
    element,
    exportedName: exportedName,
  );
  if (element is ClassElement) {
    record['classModifiers'] = <String, Object?>{
      'abstract': element.isAbstract,
      'base': element.isBase,
      'constructable': element.isConstructable,
      'extendableOutside': element.isExtendableOutside,
      'final': element.isFinal,
      'implementableOutside': element.isImplementableOutside,
      'interface': element.isInterface,
      'mixableOutside': element.isMixableOutside,
      'mixinApplication': element.isMixinApplication,
      'mixinClass': element.isMixinClass,
      'sealed': element.isSealed,
    };
  }
  if (element is MixinElement) {
    record['mixinModifiers'] = <String, Object?>{
      'base': element.isBase,
      'implementableOutside': element.isImplementableOutside,
    };
  }
  if (element is EnumElement) {
    record['enumConstants'] = <Map<String, Object?>>[
      for (final FieldElement value in element.constants) _elementRecord(value),
    ];
  }
  if (element is InterfaceElement) {
    record['constructors'] = _sortedRecords(element.constructors);
    record['interfaceMembers'] = _sortedRecords(
      element.interfaceMembers.values,
    );
  }
  if (element is InstanceElement) {
    record['properties'] = _sortedRecords(element.fields);
    record['getters'] = _sortedRecords(element.getters);
    record['setters'] = _sortedRecords(element.setters);
    record['methods'] = _sortedRecords(element.methods);
  }
  return record;
}

Object? _canonicalJsonValue(Object? value) {
  if (value is Map<String, Object?>) {
    final List<String> keys = value.keys.toList()..sort();
    return <String, Object?>{
      for (final String key in keys) key: _canonicalJsonValue(value[key]),
    };
  }
  if (value is List<Object?>) {
    return <Object?>[
      for (final Object? item in value) _canonicalJsonValue(item),
    ];
  }
  return value;
}

String _contractDigest(Map<String, Object?> contract) => sha256
    .convert(utf8.encode(jsonEncode(_canonicalJsonValue(contract))))
    .toString();

Future<Map<String, Object?>> buildPublicDartApiBaseline(
  Directory repository,
) async {
  final Directory unresolvedRoot = repository.absolute;
  if (FileSystemEntity.typeSync(unresolvedRoot.path, followLinks: false) !=
      FileSystemEntityType.directory) {
    throw const PublicDartApiError(
      'The repository root must be a directory, not a link.',
    );
  }
  final Directory root;
  try {
    root = Directory(unresolvedRoot.resolveSymbolicLinksSync());
  } on FileSystemException catch (error) {
    throw PublicDartApiError(
      'The repository root could not be resolved: ${error.message}.',
    );
  }
  final String runningSdk = Platform.version.split(' ').first;
  if (runningSdk != publicDartApiDartSdkVersion) {
    throw PublicDartApiError(
      'The public Dart API baseline requires Dart '
      '$publicDartApiDartSdkVersion, not $runningSdk.',
    );
  }
  final Map<String, String> package = _packageIdentity(root);
  final String libraryPath = '${root.path}/lib/fonix.dart';
  if (FileSystemEntity.typeSync(libraryPath, followLinks: false) !=
      FileSystemEntityType.file) {
    throw const PublicDartApiError(
      'lib/fonix.dart must be a regular file, not a link.',
    );
  }
  final List<String> dartSourcePaths = await _boundedDartSourcePaths(root);
  final AnalysisContextCollection collection = AnalysisContextCollection(
    includedPaths: <String>['${root.path}/lib'],
  );
  try {
    final session = collection.contextFor(libraryPath).currentSession;
    final result = await session.getResolvedLibrary(libraryPath);
    if (result is! ResolvedLibraryResult) {
      throw PublicDartApiError(
        'The public library could not be resolved: ${result.runtimeType}.',
      );
    }
    final List<Diagnostic> errors = <Diagnostic>[
      for (final ResolvedUnitResult unit in result.units)
        ...unit.diagnostics.where(
          (Diagnostic diagnostic) => diagnostic.severity == Severity.error,
        ),
    ];
    if (errors.isNotEmpty) {
      throw PublicDartApiError(
        'The public library has analysis errors; first: ${errors.first}.',
      );
    }
    final List<MapEntry<String, Element>> namespace =
        result.element.exportNamespace.definedNames2.entries.toList()..sort(
          (MapEntry<String, Element> left, MapEntry<String, Element> right) =>
              left.key.compareTo(right.key),
        );
    if (namespace.isEmpty) {
      throw const PublicDartApiError('The public export namespace is empty.');
    }
    final Map<String, LibraryElement> publicLibraries =
        <String, LibraryElement>{result.element.uri.toString(): result.element};
    for (final MapEntry<String, Element> entry in namespace) {
      final LibraryElement? library = entry.value.library;
      if (entry.value is MultiplyDefinedElement || library == null) {
        throw PublicDartApiError(
          'Public name ${entry.key} has no unique defining library.',
        );
      }
      publicLibraries[library.uri.toString()] = library;
    }
    final Set<String> resolvedLibraryUris = <String>{};
    while (resolvedLibraryUris.length < publicLibraries.length) {
      if (publicLibraries.length > 1024) {
        throw const PublicDartApiError(
          'The public export closure exceeds its library-count bound.',
        );
      }
      final List<String> pendingUris =
          publicLibraries.keys
              .where((String uri) => !resolvedLibraryUris.contains(uri))
              .toList()
            ..sort();
      final String uri = pendingUris.first;
      final LibraryElement library = publicLibraries[uri]!;
      final Object resolved = identical(library, result.element)
          ? result
          : await session.getResolvedLibraryByElement(library);
      if (resolved is! ResolvedLibraryResult) {
        throw PublicDartApiError(
          'Public export library $uri could not be resolved: '
          '${resolved.runtimeType}.',
        );
      }
      final List<Diagnostic> libraryErrors = <Diagnostic>[
        for (final ResolvedUnitResult unit in resolved.units)
          ...unit.diagnostics.where(
            (Diagnostic diagnostic) => diagnostic.severity == Severity.error,
          ),
      ];
      if (libraryErrors.isNotEmpty) {
        throw PublicDartApiError(
          'Public export library $uri has analysis errors; first: '
          '${libraryErrors.first}.',
        );
      }
      resolvedLibraryUris.add(uri);
      for (final LibraryFragment fragment in resolved.element.fragments) {
        for (final LibraryExport export in fragment.libraryExports) {
          final LibraryElement? exportedLibrary = export.exportedLibrary;
          if (exportedLibrary == null) {
            throw PublicDartApiError(
              'Public export library $uri has an unresolved export.',
            );
          }
          publicLibraries.putIfAbsent(
            exportedLibrary.uri.toString(),
            () => exportedLibrary,
          );
        }
      }
    }
    for (final String sourcePath in dartSourcePaths) {
      final Object sourceResult = await session.getResolvedUnit(sourcePath);
      if (sourceResult is! ResolvedUnitResult) {
        throw PublicDartApiError(
          'Package Dart source $sourcePath could not be resolved: '
          '${sourceResult.runtimeType}.',
        );
      }
      final List<Diagnostic> sourceErrors = sourceResult.diagnostics
          .where(
            (Diagnostic diagnostic) => diagnostic.severity == Severity.error,
          )
          .toList();
      if (sourceErrors.isNotEmpty) {
        throw PublicDartApiError(
          'Package Dart source $sourcePath has analysis errors; first: '
          '${sourceErrors.first}.',
        );
      }
      final _PublicDartApiSourceVisitor visitor = _PublicDartApiSourceVisitor();
      sourceResult.unit.accept(visitor);
      if (visitor.failure case final String failure) {
        throw PublicDartApiError('Package Dart source $sourcePath: $failure');
      }
    }
    final List<String> sortedPublicLibraryUris = publicLibraries.keys.toList()
      ..sort();
    final List<Map<String, Object?>> exportedLibraries = <Map<String, Object?>>[
      for (final String uri in sortedPublicLibraryUris)
        <String, Object?>{
          'identity': _elementIdentity(publicLibraries[uri]!),
          'annotations': _annotationRecords(publicLibraries[uri]!.metadata),
        },
    ];
    final Map<String, Object?> contract = <String, Object?>{
      'libraryUri': publicDartApiLibraryUri,
      'packageName': package['name'],
      'packageVersion': package['version'],
      'minimumDartSdk': package['minimumSdk'],
      'generatorDartSdk': publicDartApiDartSdkVersion,
      'analyzerVersion': publicDartApiAnalyzerVersion,
      'libraryAnnotations': _annotationRecords(result.element.metadata),
      'exportedLibraries': exportedLibraries,
      'exportedElements': <Map<String, Object?>>[
        for (final MapEntry<String, Element> entry in namespace)
          _exportedElementRecord(entry.key, entry.value),
      ],
    };
    return <String, Object?>{
      'schemaVersion': 1,
      'claimStatus': 'reviewed-public-dart-api-baseline',
      'contract': contract,
      'contractSha256': _contractDigest(contract),
      'claimBoundary': _claimBoundary,
    };
  } finally {
    await collection.dispose();
  }
}

String renderPublicDartApiBaseline(Map<String, Object?> baseline) =>
    '${const JsonEncoder.withIndent('  ').convert(_canonicalJsonValue(baseline))}\n';

void verifyPublicDartApiBaselineText({
  required String committedText,
  required String currentText,
}) {
  if (committedText == currentText) {
    return;
  }
  final List<String> committedLines = const LineSplitter().convert(
    committedText,
  );
  final List<String> currentLines = const LineSplitter().convert(currentText);
  var line = 0;
  while (line < committedLines.length &&
      line < currentLines.length &&
      committedLines[line] == currentLines[line]) {
    line += 1;
  }
  final String committed = line < committedLines.length
      ? committedLines[line]
      : '<end of committed baseline>';
  final String current = line < currentLines.length
      ? currentLines[line]
      : '<end of current baseline>';
  throw PublicDartApiError(
    'The resolved public Dart API differs from the committed baseline at '
    'line ${line + 1}.\ncommitted: $committed\ncurrent:   $current',
  );
}

Future<Map<String, Object?>> verifyPublicDartApiBaseline(
  Directory repository,
) async {
  final Map<String, Object?> actual = await buildPublicDartApiBaseline(
    repository,
  );
  final String expectedText = renderPublicDartApiBaseline(actual);
  final String committedText = _readBoundedText(
    File('${repository.absolute.path}/$publicDartApiBaselinePath'),
    'public Dart API baseline',
  );
  verifyPublicDartApiBaselineText(
    committedText: committedText,
    currentText: expectedText,
  );
  return actual;
}

Directory _defaultRepository() =>
    File.fromUri(Platform.script).absolute.parent.parent.parent;

Future<void> main(List<String> arguments) async {
  var repository = _defaultRepository();
  var printCurrent = false;
  for (var index = 0; index < arguments.length; index += 1) {
    final String argument = arguments[index];
    if (argument == '--print-current') {
      printCurrent = true;
    } else if (argument == '--repository' && index + 1 < arguments.length) {
      repository = Directory(arguments[++index]);
    } else {
      stderr.writeln(
        'usage: dart --packages=.dart_tool/package_config.json '
        'tool/ci/verify_public_dart_api.dart '
        '[--repository PATH] [--print-current]',
      );
      exitCode = 2;
      return;
    }
  }
  try {
    final Map<String, Object?> baseline = printCurrent
        ? await buildPublicDartApiBaseline(repository)
        : await verifyPublicDartApiBaseline(repository);
    if (printCurrent) {
      stdout.write(renderPublicDartApiBaseline(baseline));
      return;
    }
    final Map<String, Object?> contract =
        baseline['contract']! as Map<String, Object?>;
    final List<Object?> exports =
        contract['exportedElements']! as List<Object?>;
    stdout.writeln(
      'Verified public Dart API baseline '
      'sha256=${baseline['contractSha256']} exports=${exports.length}.',
    );
  } on Object catch (error) {
    stderr.writeln('public Dart API baseline verification failed: $error');
    exitCode = 1;
  }
}
