import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/android_xnnpack_qualification.dart';
import 'package:fonix_reference/src/inference_backend.dart';
import 'package:fonix_reference/src/reference_smoke.dart';

void main() {
  test('owns one exact bounded path-free XNNPACK receipt', () {
    final Map<String, Object?> decoded = _decodedReceipt();

    final AndroidXnnpackQualificationReceipt receipt =
        AndroidXnnpackQualificationReceipt.fromMap(decoded);

    expect(receipt.toJsonString(), androidXnnpackExpectedReceiptJson);
    expect(utf8.encode(receipt.toJsonString()).length, lessThan(3 * 1024));
    expect(receipt.toJsonString(), isNot(contains('\n')));
    expect(receipt.toJsonString(), isNot(contains('\r')));
    expect(receipt.toJsonString(), isNot(contains('/private/')));
    expect(receipt.toJsonString(), isNot(contains('/data/')));
    expect(receipt.toMap()['smokeProfile'], 'xnnpack');
    expect(() => receipt.toMap()['status'] = 'failed', throwsUnsupportedError);
  });

  test('rejects nested type, value, key, and cardinality changes', () {
    final List<void Function(Map<String, Object?>)> tamperers =
        <void Function(Map<String, Object?>)>[
          (Map<String, Object?> value) => value['extra'] = true,
          (Map<String, Object?> value) => value.remove('runtime'),
          (Map<String, Object?> value) =>
              (value['runtime']! as Map<String, Object?>)['platform'] = 'linux',
          (Map<String, Object?> value) =>
              (value['xnnpack']! as Map<String, Object?>)['validatedRuns'] =
                  true,
          (Map<String, Object?> value) =>
              ((value['xnnpackProvider']! as Map<String, Object?>)['options']!
                      as Map<String, Object?>)['intra_op_num_threads'] =
                  1,
          (Map<String, Object?> value) =>
              ((value['fallback']!
                          as Map<
                            String,
                            Object?
                          >)['reportNodeExecutionsByProvider']!
                      as Map<String, Object?>)['cpu'] =
                  2,
          (Map<String, Object?> value) =>
              ((value['cpuReference']! as Map<String, Object?>)['outputValues']!
                      as List<Object?>)
                  .removeLast(),
        ];

    for (final void Function(Map<String, Object?>) tamper in tamperers) {
      final Map<String, Object?> value = _decodedReceipt();
      tamper(value);
      expect(
        () => AndroidXnnpackQualificationReceipt.fromMap(value),
        throwsA(isA<InferenceBackendFailure>()),
      );
    }
  });

  test('uses the shared channel schema and receipt byte bound', () {
    final Map<String, Object?> decoded = _decodedReceipt();
    expect(decoded['schemaVersion'], androidXnnpackQualificationSchemaVersion);
    expect(decoded['schemaVersion'], referenceSmokeSchemaVersion);
    expect(
      utf8.encode(androidXnnpackExpectedReceiptJson).length,
      lessThanOrEqualTo(maximumReferenceSmokeReceiptBytes),
    );
  });

  test(
    'failure cleanup recursively retires a non-empty private root',
    () async {
      final Directory root = await Directory.systemTemp.createTemp(
        'fonix-xnnpack-failure-cleanup-test-',
      );
      final Directory nested = Directory('${root.path}/nested');
      await nested.create();
      await File('${nested.path}/profile.json').writeAsString('{}');

      await retireAndroidXnnpackArtifactRootForTesting(
        root,
        requireEmpty: false,
      );

      expect(
        await FileSystemEntity.type(root.path, followLinks: false),
        FileSystemEntityType.notFound,
      );
    },
  );

  test('success cleanup reports residue but still retires its root', () async {
    final Directory root = await Directory.systemTemp.createTemp(
      'fonix-xnnpack-success-cleanup-test-',
    );
    await File('${root.path}/profile.json').writeAsString('{}');

    await expectLater(
      retireAndroidXnnpackArtifactRootForTesting(root, requireEmpty: true),
      throwsA(isA<InferenceBackendFailure>()),
    );
    expect(
      await FileSystemEntity.type(root.path, followLinks: false),
      FileSystemEntityType.notFound,
    );
  });

  test('success cleanup accepts and removes an empty private root', () async {
    final Directory root = await Directory.systemTemp.createTemp(
      'fonix-xnnpack-empty-cleanup-test-',
    );

    await retireAndroidXnnpackArtifactRootForTesting(root, requireEmpty: true);

    expect(
      await FileSystemEntity.type(root.path, followLinks: false),
      FileSystemEntityType.notFound,
    );
  });
}

Map<String, Object?> _decodedReceipt() =>
    (jsonDecode(androidXnnpackExpectedReceiptJson) as Map<Object?, Object?>)
        .cast<String, Object?>();
