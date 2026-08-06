import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/runtime.dart';
import 'package:test/test.dart';

void main() {
  group('runtime source', () {
    test('explicit file requires an absolute in-root path', () {
      expect(
        () => OrtRuntimeSource.file(absolutePath: 'libonnxruntime.so'),
        throwsArgumentError,
      );
      expect(
        () => OrtRuntimeSource.file(
          absolutePath: '/trusted-other/libonnxruntime.so',
          allowedRoot: '/trusted',
        ),
        throwsArgumentError,
      );

      final source = OrtRuntimeSource.file(
        absolutePath: '/trusted/runtime/../libonnxruntime.so',
        allowedRoot: '/trusted',
      );
      expect(source.kind, OrtRuntimeSourceKind.file);
      expect(source.libraryPath, '/trusted/libonnxruntime.so');
      expect(source.allowedRoot, '/trusted');
    });

    test('process names are constrained, unique, copied, and bounded', () {
      final callerNames = <String>['libonnxruntime.so'];
      final source = OrtRuntimeSource.process(
        preferredLibraryNames: callerNames,
      );
      callerNames[0] = 'changed.so';

      expect(source.preferredLibraryNames, <String>['libonnxruntime.so']);
      expect(
        () => source.preferredLibraryNames.add('other.so'),
        throwsUnsupportedError,
      );
      expect(
        () => OrtRuntimeSource.process(
          preferredLibraryNames: <String>['../libonnxruntime.so'],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtRuntimeSource.process(
          preferredLibraryNames: <String>['ort.so', 'ort.so'],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtRuntimeSource.process(
          preferredLibraryNames: <String>[
            '${List<String>.filled(125, 'a').join()}é.so',
          ],
        ),
        throwsArgumentError,
      );
    });
  });

  group('shape arithmetic', () {
    test('rank-zero scalar contains exactly one element', () {
      final shape = OrtShape(const <int>[]);
      expect(shape.isScalar, isTrue);
      expect(shape.elementCount, 1);
      expect(shape.requiredBytes(4), 4);
    });

    test('zero dimension has zero storage without skipping validation', () {
      final shape = OrtShape(const <int>[2, 0, 4]);
      expect(shape.elementCount, 0);
      expect(shape.requiredBytes(8), 0);
      expect(
        () => OrtShape(const <int>[
          0,
          5,
        ], limits: OrtResourceLimits(maxDimension: 4)),
        throwsRangeError,
      );
    });

    test('element and byte overflow are rejected before allocation', () {
      expect(
        () => OrtShape(const <int>[
          8,
          8,
        ], limits: OrtResourceLimits(maxTensorElements: 63)),
        throwsRangeError,
      );
      final shape = OrtShape(const <int>[
        4,
      ], limits: OrtResourceLimits(maxTensorBytes: 15));
      expect(() => shape.requiredBytes(4), throwsRangeError);
    });

    test(
      'iterable shapes and Boolean values stop at their configured bound',
      () {
        var shapeReads = 0;
        Iterable<int> endlessShape() sync* {
          while (true) {
            shapeReads += 1;
            yield 1;
          }
        }

        expect(
          () => OrtShape(endlessShape(), limits: OrtResourceLimits(maxRank: 2)),
          throwsRangeError,
        );
        expect(shapeReads, 3);

        var valueReads = 0;
        Iterable<bool> endlessValues() sync* {
          while (true) {
            valueReads += 1;
            yield true;
          }
        }

        expect(
          () => normalizeOrtBoolValuesForTesting(
            endlessValues(),
            shape: const <int>[2],
          ),
          throwsArgumentError,
        );
        expect(valueReads, 3);
        expect(
          normalizeOrtBoolValuesForTesting(
            <bool>[true, false],
            shape: const <int>[2],
          ),
          <int>[1, 0],
        );
      },
    );
  });

  group('providers and options', () {
    test('provider options are copied and sensitive values are redacted', () {
      final callerOptions = <String, String>{
        'device_id': '0',
        'cache_path': '/private/model-cache',
      };
      final provider = OrtExecutionProvider.named(
        'custom',
        options: callerOptions,
      );
      callerOptions['device_id'] = '1';

      expect(provider.options['device_id'], '0');
      expect(provider.redactedOptions, <String, String>{
        'device_id': '0',
        'cache_path': '<redacted>',
      });
      expect(() => provider.options['x'] = 'y', throwsUnsupportedError);
      expect(
        () => OrtExecutionProvider.named(
          'custom',
          options: <String, String>{
            List<String>.filled(65, 'é').join(): 'value',
          },
        ),
        throwsArgumentError,
      );
    });

    test('default session policy is explicit CPU with immutable state', () {
      final options = OrtSessionOptions();
      expect(options.providers.single.id, 'cpu');
      expect(
        options.providers.single.requirement,
        OrtProviderRequirement.required,
      );
      expect(options.fallbackPolicy, OrtFallbackPolicy.report);
      expect(
        () => options.providers.add(OrtExecutionProvider.cpu()),
        throwsUnsupportedError,
      );
    });

    test('known provider threading constraints fail locally', () {
      expect(
        () => OrtSessionOptions(
          providers: <OrtExecutionProvider>[OrtExecutionProvider.directMl()],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          executionMode: OrtExecutionMode.sequential,
          enableMemoryPattern: false,
          intraOpThreads: 4,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.xnnpack(intraOpThreads: 4),
          ],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.xnnpack(intraOpThreads: 4),
          ],
        ),
        throwsArgumentError,
      );
      final xnnpack = OrtSessionOptions(
        intraOpThreads: 1,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.xnnpack(intraOpThreads: 1024),
        ],
        fallbackPolicy: OrtFallbackPolicy.allow,
      );
      expect(xnnpack.intraOpThreads, 1);
      expect(xnnpack.providers.single.options, <String, String>{
        'intra_op_num_threads': '1024',
      });
      final valid = OrtSessionOptions(
        executionMode: OrtExecutionMode.sequential,
        enableMemoryPattern: false,
        providers: <OrtExecutionProvider>[OrtExecutionProvider.directMl()],
        fallbackPolicy: OrtFallbackPolicy.allow,
      );
      expect(valid.providers.single.id, 'directml');
    });

    test('typed mobile provider options are closed and normalized', () {
      final xnnpack = OrtExecutionProvider.xnnpack(intraOpThreads: 3);
      expect(xnnpack.options, <String, String>{'intra_op_num_threads': '3'});
      expect(
        () => OrtExecutionProvider.xnnpack(intraOpThreads: 0),
        throwsRangeError,
      );
      expect(
        () => OrtExecutionProvider.xnnpack(intraOpThreads: 1025),
        throwsRangeError,
      );
      for (final String value in const <String>['1', '1024']) {
        expect(
          OrtExecutionProvider.named(
            'xnnpack',
            options: <String, String>{'intra_op_num_threads': value},
          ).options,
          <String, String>{'intra_op_num_threads': value},
        );
      }
      for (final Map<String, String> options in <Map<String, String>>[
        const <String, String>{},
        const <String, String>{'threads': '1'},
        const <String, String>{'intra_op_num_threads': ''},
        const <String, String>{'intra_op_num_threads': '-1'},
        const <String, String>{'intra_op_num_threads': '+1'},
        const <String, String>{'intra_op_num_threads': '0'},
        const <String, String>{'intra_op_num_threads': '01'},
        const <String, String>{'intra_op_num_threads': '1.0'},
        const <String, String>{'intra_op_num_threads': ' 1'},
        const <String, String>{'intra_op_num_threads': '1 '},
        const <String, String>{'intra_op_num_threads': '1025'},
      ]) {
        expect(
          () => OrtExecutionProvider.named('xnnpack', options: options),
          throwsArgumentError,
        );
      }

      final coreMl = OrtExecutionProvider.coreMl(
        modelFormat: OrtCoreMlModelFormat.mlProgram,
        computeUnits: OrtCoreMlComputeUnits.cpuAndNeuralEngine,
        requireStaticInputShapes: true,
        enableOnSubgraphs: true,
      );
      expect(coreMl.options, <String, String>{
        'ModelFormat': 'MLProgram',
        'MLComputeUnits': 'CPUAndNeuralEngine',
        'RequireStaticInputShapes': '1',
        'EnableOnSubgraphs': '1',
      });

      final nnapi = OrtExecutionProvider.nnapi(
        flags: const <OrtNnapiFlag>{
          OrtNnapiFlag.useFp16,
          OrtNnapiFlag.cpuDisabled,
        },
      );
      expect(nnapi.options, <String, String>{'flags': '5'});
      expect(
        () => OrtExecutionProvider.nnapi(
          flags: const <OrtNnapiFlag>{
            OrtNnapiFlag.cpuDisabled,
            OrtNnapiFlag.cpuOnly,
          },
        ),
        throwsArgumentError,
      );
    });

    test('Core ML cache input is identity-scoped and root-bound', () {
      const String modelSha256 =
          '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
      final OrtCoreMlCacheConfiguration cache = OrtCoreMlCacheConfiguration(
        rootDirectory: '/private/app/cache',
        modelSha256: modelSha256,
        applicationSchema: 'character-model-v2',
      );
      final OrtExecutionProvider coreMl = OrtExecutionProvider.coreMl(
        cache: cache,
      );
      expect(coreMl.coreMlCache, same(cache));
      expect(coreMl.options, isNot(contains('ModelCacheDirectory')));
      expect(
        () => OrtExecutionProvider.named(
          'coreml',
          options: const <String, String>{
            'ModelCacheDirectory': '/private/app/cache/chosen-by-caller',
          },
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtCoreMlCacheConfiguration(
          rootDirectory: 'relative/cache',
          modelSha256: modelSha256,
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtCoreMlCacheConfiguration(
          rootDirectory: '/private/app/cache',
          modelSha256: modelSha256.toUpperCase(),
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(providers: <OrtExecutionProvider>[coreMl]),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          artifactRoot: '/private/app/other',
          providers: <OrtExecutionProvider>[coreMl],
        ),
        throwsArgumentError,
      );
      final OrtSessionOptions valid = OrtSessionOptions(
        artifactRoot: cache.rootDirectory,
        providers: <OrtExecutionProvider>[coreMl, OrtExecutionProvider.cpu()],
      );
      expect(valid.artifactRoot, cache.rootDirectory);
    });

    test('duplicate provider IDs are rejected', () {
      expect(
        () => OrtSessionOptions(
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.cpu(),
            OrtExecutionProvider.cpu(),
          ],
        ),
        throwsArgumentError,
      );
    });

    test('explicit CPU fallback must remain last in provider order', () {
      expect(
        () => OrtSessionOptions(
          fallbackPolicy: OrtFallbackPolicy.allow,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.cpu(),
            OrtExecutionProvider.xnnpack(),
          ],
        ),
        throwsArgumentError,
      );
      final OrtSessionOptions valid = OrtSessionOptions(
        fallbackPolicy: OrtFallbackPolicy.allow,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.xnnpack(),
          OrtExecutionProvider.cpu(),
        ],
      );
      expect(
        valid.providers.map((OrtExecutionProvider value) => value.id),
        <String>['xnnpack', 'cpu'],
      );
    });
  });

  group('element types', () {
    test('API-27 values round-trip without implying creation support', () {
      for (final type in OrtTensorElementType.values) {
        expect(OrtTensorElementType.fromNativeValue(type.nativeValue), type);
      }
      expect(OrtTensorElementType.float32.supportsDenseCreation, isTrue);
      expect(OrtTensorElementType.float16.supportsDenseCreation, isTrue);
      expect(OrtTensorElementType.bfloat16.supportsDenseCreation, isTrue);
      expect(OrtTensorElementType.uint4.fixedStorageBytes, isNull);
      expect(
        () => OrtTensorElementType.fromNativeValue(999),
        throwsArgumentError,
      );
    });

    test('metadata values are closed, validated, and immutable', () {
      expect(() => OrtDimension.fixed(-1), throwsRangeError);
      expect(() => OrtDimension.dynamic(''), throwsArgumentError);
      expect(() => OrtTypeInfo(kind: OrtValueKind.tensor), throwsArgumentError);
      expect(
        () => OrtTypeInfo(
          kind: OrtValueKind.map,
          mapKeyType: OrtTensorElementType.float32,
          mapValueType: OrtTypeInfo(
            kind: OrtValueKind.tensor,
            tensorElementType: OrtTensorElementType.float32,
          ),
        ),
        throwsArgumentError,
      );

      final callerCustom = <String, String>{'license': 'MIT'};
      final metadata = OrtModelMetadata(custom: callerCustom);
      callerCustom['license'] = 'changed';
      expect(metadata.custom, <String, String>{'license': 'MIT'});
      expect(() => metadata.custom['other'] = 'value', throwsUnsupportedError);
    });
  });

  group('diagnostics', () {
    test('strict schema round-trips the distinct provider states', () {
      final source = _diagnosticsJson();
      final diagnostics = OrtDiagnostics.fromJsonString(jsonEncode(source));

      expect(diagnostics.runtimeVersion, '1.27.1');
      expect(diagnostics.runtimeMode, OrtRuntimeMode.file);
      expect(diagnostics.providers.single.registered, isTrue);
      expect(diagnostics.providers.single.active, isNull);
      expect(diagnostics.toJson(), source);

      final external = _diagnosticsJson()..['artifactSha256'] = null;
      expect(OrtDiagnostics.fromJson(external).artifactSha256, isNull);
    });

    test(
      'unknown fields, invalid digests, and oversized payloads fail closed',
      () {
        final unknown = _diagnosticsJson()..['privatePath'] = '/Users/alice';
        expect(() => OrtDiagnostics.fromJson(unknown), throwsFormatException);
        final invalidDigest = _diagnosticsJson()
          ..['artifactSha256'] = 'not-a-digest';
        expect(
          () => OrtDiagnostics.fromJson(invalidDigest),
          throwsFormatException,
        );
        expect(
          () => OrtDiagnostics.fromJsonString(
            jsonEncode(_diagnosticsJson()),
            limits: OrtResourceLimits(maxDiagnosticsBytes: 8),
          ),
          throwsFormatException,
        );
      },
    );
  });

  group('native value boundary guards', () {
    test('rejects malformed kinds, child counts, and lease lengths', () {
      expect(
        () => validateOrtNativeValueKindForTesting(1, OrtValueKind.tensor),
        returnsNormally,
      );
      expect(
        () => validateOrtNativeValueKindForTesting(3, OrtValueKind.tensor),
        throwsFormatException,
      );
      expect(
        () => validateOrtNativeValueKindForTesting(-1, OrtValueKind.unknown),
        throwsFormatException,
      );

      expect(validateOrtValueChildCountForTesting(1024), 1024);
      expect(
        () => validateOrtValueChildCountForTesting(1025),
        throwsFormatException,
      );
      expect(
        () => validateOrtValueChildCountForTesting(-1),
        throwsFormatException,
      );

      expect(
        () => validateOrtDataLeaseLengthForTesting(16, 16),
        returnsNormally,
      );
      expect(
        () => validateOrtDataLeaseLengthForTesting(15, 16),
        throwsFormatException,
      );
      expect(
        () => validateOrtDataLeaseLengthForTesting(-1, 0),
        throwsFormatException,
      );
    });
  });
}

Map<String, Object?> _diagnosticsJson() => <String, Object?>{
  'schemaVersion': 1,
  'dartPackageVersion': fonixPackageVersion,
  'shimAbiVersion': fonixShimAbiVersion,
  'shimBuildId': 'source-preview',
  'requiredOrtApiVersion': 27,
  'negotiatedOrtApiVersion': 27,
  'runtimeVersion': '1.27.1',
  'runtimeOwner': 'application',
  'runtimeMode': 'file',
  'runtimeIdentity': 'sha256:runtime',
  'platform': 'macos',
  'architecture': 'arm64',
  'artifactFlavor': 'external',
  'artifactSha256': List<String>.filled(64, 'a').join(),
  'modelId': null,
  'session': null,
  'providers': <Object?>[
    <String, Object?>{
      'wrapperId': 'cpu',
      'registrationMechanism': 'implicit',
      'registrationName': null,
      'reportedName': 'CPUExecutionProvider',
      'compiled': true,
      'discoverable': true,
      'registered': true,
      'active': null,
      'qualified': false,
      'assignmentEvidence': null,
      'fallbackReason': null,
      'options': <String, String>{},
    },
  ],
};
