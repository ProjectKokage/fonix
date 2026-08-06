import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:test/test.dart';

void main() {
  const names = <String, String>{
    'coreml': 'CoreMLExecutionProvider',
    'cpu': 'CPUExecutionProvider',
  };

  group('provider run evidence', () {
    test('copies one bounded run into closed provider counts', () {
      final evidence = OrtProviderRunEvidence.fromOrtProfileJson(
        _profile(<String>[
          'CoreMLExecutionProvider',
          'CoreMLExecutionProvider',
          'CPUExecutionProvider',
        ]),
        reportedProviderNames: names,
      );

      expect(evidence.nodeExecutionCount, 3);
      expect(evidence.nodeExecutionsByProvider, <String, int>{
        'coreml': 2,
        'cpu': 1,
      });
      expect(evidence.activeProviderIds, <String>{'coreml', 'cpu'});
      expect(evidence.isActive('coreml'), isTrue);
      expect(evidence.isFullyAssignedTo('coreml'), isFalse);
      expect(
        () => evidence.nodeExecutionsByProvider['cpu'] = 0,
        throwsUnsupportedError,
      );
    });

    test('enforces active, full assignment, and fallback independently', () {
      final full = OrtProviderRunEvidence.fromOrtProfileJson(
        _profile(<String>['CoreMLExecutionProvider']),
        reportedProviderNames: names,
      );
      final strict = <OrtExecutionProvider>[
        OrtExecutionProvider.coreMl(
          requirement: OrtProviderRequirement.requireFullAssignment,
        ),
        OrtExecutionProvider.cpu(),
      ];
      expect(
        () => full.enforce(
          providers: strict,
          fallbackPolicy: OrtFallbackPolicy.rejectAny,
        ),
        returnsNormally,
      );

      final fallback = OrtProviderRunEvidence.fromOrtProfileJson(
        _profile(<String>['CoreMLExecutionProvider', 'CPUExecutionProvider']),
        reportedProviderNames: names,
      );
      expect(
        () => fallback.enforce(
          providers: strict,
          fallbackPolicy: OrtFallbackPolicy.allow,
        ),
        throwsA(isA<OrtProviderEvidenceException>()),
      );
      expect(
        () => fallback.enforce(
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.coreMl(),
            OrtExecutionProvider.cpu(),
          ],
          fallbackPolicy: OrtFallbackPolicy.rejectCpu,
        ),
        throwsA(isA<OrtProviderEvidenceException>()),
      );
    });

    test('rejects unknown providers, malformed traces, and multiple runs', () {
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          _profile(<String>['InjectedExecutionProvider']),
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          jsonEncode(<Object?>[
            _event('CPUExecutionProvider', 0),
            _modelRun,
            _modelRun,
          ]),
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          '{}',
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          _profile(<String>[]),
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          '${List<String>.filled(33, '[').join()}'
          '${List<String>.filled(33, ']').join()}',
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          jsonEncode(<Object?>[
            _event('CPUExecutionProvider', 0, timestamp: 999),
            _modelRun,
          ]),
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          jsonEncode(<Object?>[
            <String, Object?>{..._event('CPUExecutionProvider', 0), 'ph': 'B'},
            _modelRun,
          ]),
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
    });

    test('rejects ambiguous provider identity maps', () {
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          _profile(<String>['CPUExecutionProvider']),
          reportedProviderNames: const <String, String>{
            'cpu': 'CPUExecutionProvider',
            'other': 'CPUExecutionProvider',
          },
        ),
        throwsArgumentError,
      );
    });

    test('rejects duplicate keys before JSON decoding can collapse them', () {
      const duplicateProvider =
          '[{"cat":"Node","name":"node_0_kernel_time",'
          '"pid":7,"tid":11,"ts":1100,"dur":5,"ph":"X",'
          '"args":{"provider":"CPUExecutionProvider",'
          '"provider":"CoreMLExecutionProvider","node_index":"0",'
          '"op_name":"Mul"}},'
          '{"cat":"Session","name":"model_run","pid":7,"tid":11,'
          '"ts":1000,"dur":1000,"ph":"X","args":{}}]';
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          duplicateProvider,
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );

      const escapedDuplicate =
          '[{"cat":"Node","name":"node_0_kernel_time",'
          '"pid":7,"tid":11,"ts":1100,"dur":5,"ph":"X",'
          '"args":{"provider":"CPUExecutionProvider",'
          '"pro\\u0076ider":"CPUExecutionProvider","node_index":"0",'
          '"op_name":"Mul"}},'
          '{"cat":"Session","name":"model_run","pid":7,"tid":11,'
          '"ts":1000,"dur":1000,"ph":"X","args":{}}]';
      expect(
        () => OrtProviderRunEvidence.fromOrtProfileJson(
          escapedDuplicate,
          reportedProviderNames: names,
        ),
        throwsFormatException,
      );
    });
  });
}

String _profile(List<String> providers) => jsonEncode(<Object?>[
  for (var index = 0; index < providers.length; index += 1)
    _event(providers[index], index),
  _modelRun,
]);

Map<String, Object?> _event(String provider, int index, {int? timestamp}) =>
    <String, Object?>{
      'cat': 'Node',
      'name': 'node_${index}_kernel_time',
      'pid': 7,
      'tid': 11,
      'ts': timestamp ?? 1100 + index * 10,
      'dur': 5,
      'ph': 'X',
      'args': <String, Object?>{
        'provider': provider,
        'node_index': index.toString(),
        'op_name': 'Mul',
      },
    };

const Map<String, Object?> _modelRun = <String, Object?>{
  'cat': 'Session',
  'name': 'model_run',
  'pid': 7,
  'tid': 11,
  'ts': 1000,
  'dur': 1000,
  'ph': 'X',
  'args': <String, Object?>{},
};
