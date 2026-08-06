import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/provider_discovery.dart';
import 'package:test/test.dart';

void main() {
  group('provider discovery', () {
    test('keeps known and unknown runtime-reported identities distinct', () {
      final OrtProviderDiscovery discovery =
          OrtProviderDiscovery.fromNativeJson(
            jsonEncode(<String, Object?>{
              'schemaVersion': 1,
              'reportedNames': <String>[
                'CPUExecutionProvider',
                'CoreMLExecutionProvider',
                'FutureExecutionProvider',
              ],
            }),
          );

      expect(discovery.knownWrapperIds, <String>{'cpu', 'coreml'});
      expect(discovery.isDiscoverable('coreml'), isTrue);
      expect(discovery.isDiscoverable('future'), isFalse);
      expect(discovery.providers.last.wrapperId, isNull);
      expect(discovery.providers.last.isKnownToFonix, isFalse);
      expect(discovery.evidenceProviderNames, <String, String>{
        'cpu': 'CPUExecutionProvider',
        'coreml': 'CoreMLExecutionProvider',
      });
    });

    test('fails closed on unknown schema, names, duplicates, and bounds', () {
      for (final Object? payload in <Object?>[
        <String, Object?>{'schemaVersion': 2, 'reportedNames': <String>[]},
        <String, Object?>{
          'schemaVersion': 1,
          'reportedNames': <String>['CPUExecutionProvider', '../provider'],
        },
        <String, Object?>{
          'schemaVersion': 1,
          'reportedNames': <String>[
            'CPUExecutionProvider',
            'CPUExecutionProvider',
          ],
        },
        <String, Object?>{
          'schemaVersion': 1,
          'reportedNames': List<String>.filled(17, 'CPUExecutionProvider'),
        },
        <String, Object?>{
          'schemaVersion': 1,
          'reportedNames': <String>[],
          'privatePath': '/private/model',
        },
      ]) {
        expect(
          () => OrtProviderDiscovery.fromNativeJson(jsonEncode(payload)),
          throwsFormatException,
        );
      }
      expect(
        () => OrtProviderDiscovery.fromNativeJson(
          '{"schemaVersion":1,"schema\\u0056ersion":1,'
          '"reportedNames":["CPUExecutionProvider"]}',
        ),
        throwsFormatException,
      );
    });

    test('diagnostics never infer compiled, active, or qualified state', () {
      final OrtProviderDiscovery discovery =
          OrtProviderDiscovery.fromNativeJson(
            '{"schemaVersion":1,"reportedNames":'
            '["CPUExecutionProvider","CoreMLExecutionProvider"]}',
          );
      final List<OrtProviderDiagnostics> diagnostics =
          buildOrtProviderDiagnostics(
            requestedProviders: <OrtExecutionProvider>[
              OrtExecutionProvider.coreMl(),
              OrtExecutionProvider.cpu(),
            ],
            discovery: discovery,
            sessionCreated: true,
          );
      final OrtProviderDiagnostics coreMl = diagnostics.singleWhere(
        (OrtProviderDiagnostics value) => value.wrapperId == 'coreml',
      );
      expect(coreMl.compiled, isNull);
      expect(coreMl.discoverable, isTrue);
      expect(coreMl.registered, isTrue);
      expect(coreMl.active, isNull);
      expect(coreMl.qualified, isNull);
      expect(coreMl.registrationName, 'CoreML');
    });

    test('uses only the embedded lock inventory for compiled state', () {
      final OrtProviderDiscovery discovery =
          OrtProviderDiscovery.fromNativeJson(
            '{"schemaVersion":1,"reportedNames":'
            '["CPUExecutionProvider","CoreMLExecutionProvider"]}',
          );
      final diagnostics = buildOrtProviderDiagnostics(
        requestedProviders: <OrtExecutionProvider>[
          OrtExecutionProvider.coreMl(),
          OrtExecutionProvider.cpu(),
        ],
        discovery: discovery,
        sessionCreated: true,
        compiledProviders: const <String, String?>{
          'cpu': 'CPUExecutionProvider',
          'locked_only': 'LockedOnlyExecutionProvider',
        },
      );

      final coreMl = diagnostics.singleWhere(
        (value) => value.wrapperId == 'coreml',
      );
      expect(coreMl.compiled, isFalse);
      expect(coreMl.discoverable, isTrue);

      final lockedOnly = diagnostics.singleWhere(
        (value) => value.wrapperId == 'locked_only',
      );
      expect(lockedOnly.compiled, isTrue);
      expect(lockedOnly.discoverable, isFalse);
      expect(lockedOnly.registered, isFalse);
      expect(lockedOnly.reportedName, 'LockedOnlyExecutionProvider');
    });
  });
}
