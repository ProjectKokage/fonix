import 'dart:io';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/isolate_protocol.dart'
    show roundTripOrtWorkerSessionOptionsForTesting;
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  group('worker option protocol', () {
    test('preserves typed Core ML cache identity inputs', () {
      final String root = p.join(
        Directory.systemTemp.absolute.path,
        'fonix-worker-cache-contract',
      );
      final String modelSha256 = List<String>.filled(64, 'a').join();
      final OrtSessionOptions original = OrtSessionOptions(
        artifactRoot: root,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.coreMl(
            modelFormat: OrtCoreMlModelFormat.mlProgram,
            computeUnits: OrtCoreMlComputeUnits.cpuOnly,
            requireStaticInputShapes: true,
            enableOnSubgraphs: true,
            cache: OrtCoreMlCacheConfiguration(
              rootDirectory: root,
              modelSha256: modelSha256,
              applicationSchema: 'worker-v2',
            ),
            requirement: OrtProviderRequirement.requireFullAssignment,
          ),
          OrtExecutionProvider.cpu(),
        ],
        fallbackPolicy: OrtFallbackPolicy.rejectAny,
      );

      final OrtSessionOptions restored =
          roundTripOrtWorkerSessionOptionsForTesting(original);
      final OrtExecutionProvider coreMl = restored.providers.first;
      expect(coreMl.options, original.providers.first.options);
      expect(coreMl.requirement, OrtProviderRequirement.requireFullAssignment);
      expect(coreMl.coreMlCache?.rootDirectory, root);
      expect(coreMl.coreMlCache?.modelSha256, modelSha256);
      expect(coreMl.coreMlCache?.applicationSchema, 'worker-v2');
      expect(restored.artifactRoot, root);
      expect(restored.fallbackPolicy, OrtFallbackPolicy.rejectAny);
    });
  });
}
