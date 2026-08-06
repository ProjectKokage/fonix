import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path;
import 'package:test/test.dart';

import '../bin/fonix_prepare_flutter_assets.dart' as asset_cli;

void main() {
  group('FonixFlutterAssetOptions', () {
    final absolute = Directory.systemTemp.absolute.path;
    final valid = <String>[
      '--target-os',
      'macos',
      '--architecture',
      'arm64',
      '--variant',
      'default',
      '--cache',
      absolute,
      '--output',
      path.join(absolute, 'fonix-assets'),
    ];

    test('rejects unknown, duplicate, missing, and valueless options', () {
      expect(
        () => asset_cli.FonixFlutterAssetOptions.parse(<String>[
          ...valid,
          '--unknown',
          'value',
        ]),
        throwsFormatException,
      );
      expect(
        () => asset_cli.FonixFlutterAssetOptions.parse(<String>[
          ...valid,
          '--cache',
          absolute,
        ]),
        throwsFormatException,
      );
      expect(
        () => asset_cli.FonixFlutterAssetOptions.parse(<String>['--target-os']),
        throwsFormatException,
      );
      expect(
        () => asset_cli.FonixFlutterAssetOptions.parse(<String>[
          '--target-os',
          '',
        ]),
        throwsFormatException,
      );
    });

    test(
      'requires cache or mirror and every supplied filesystem path absolute',
      () {
        expect(
          () => asset_cli.FonixFlutterAssetOptions.parse(<String>[
            '--target-os',
            'macos',
            '--architecture',
            'arm64',
            '--variant',
            'default',
            '--output',
            path.join(absolute, 'fonix-assets'),
          ]),
          throwsFormatException,
        );
        for (final option in <String>[
          '--package-root',
          '--cache',
          '--mirror',
          '--output',
        ]) {
          final arguments = <String>[...valid];
          if (option == '--cache' || option == '--output') {
            arguments[arguments.indexOf(option) + 1] = 'relative/path';
          } else {
            arguments.addAll(<String>[option, 'relative/path']);
          }
          expect(
            () => asset_cli.FonixFlutterAssetOptions.parse(arguments),
            throwsFormatException,
            reason: option,
          );
        }
      },
    );
  });

  group('Flutter asset publication', () {
    late Directory temporary;

    setUp(() {
      temporary = Directory.systemTemp.createTempSync('fonix-asset-publish-');
    });

    tearDown(() {
      temporary.deleteSync(recursive: true);
    });

    test('rejects symbolic-link and non-directory outputs', () {
      final realDirectory = Directory(path.join(temporary.path, 'real'))
        ..createSync();
      final link = Link(path.join(temporary.path, 'link'))
        ..createSync(realDirectory.path);
      final file = File(path.join(temporary.path, 'file'))
        ..writeAsStringSync('x');

      expect(
        () => asset_cli.validateFonixFlutterAssetOutputDirectory(
          Directory(link.path),
        ),
        throwsFormatException,
      );
      expect(
        () => asset_cli.validateFonixFlutterAssetOutputDirectory(
          Directory(file.path),
        ),
        throwsFormatException,
      );
    });

    test(
      'rejects nonregular destinations and pre-existing temporary files',
      () {
        final source = File(path.join(temporary.path, 'source'))
          ..writeAsBytesSync(<int>[1, 2, 3]);
        final directoryDestination = Directory(
          path.join(temporary.path, 'destination'),
        )..createSync();
        expect(
          () => asset_cli.publishFonixFlutterAsset(
            source,
            File(directoryDestination.path),
          ),
          throwsFormatException,
        );

        final destination = File(path.join(temporary.path, 'asset'));
        File('${destination.path}.tmp-17').writeAsStringSync('occupied');
        expect(
          () => asset_cli.publishFonixFlutterAsset(
            source,
            destination,
            publisherProcessId: 17,
          ),
          throwsFormatException,
        );
      },
    );

    test('publishes exact bytes and supports an idempotent rerun', () {
      final source = File(path.join(temporary.path, 'source'))
        ..writeAsBytesSync(<int>[0, 1, 2, 255]);
      final destination = File(path.join(temporary.path, 'asset'));

      asset_cli.publishFonixFlutterAsset(source, destination);
      expect(destination.readAsBytesSync(), source.readAsBytesSync());
      asset_cli.publishFonixFlutterAsset(source, destination);
      expect(destination.readAsBytesSync(), source.readAsBytesSync());
    });
  });

  test('executable rejects an unlocked target tuple', () async {
    final temporary = Directory.systemTemp.createTempSync('fonix-asset-cli-');
    addTearDown(() => temporary.deleteSync(recursive: true));
    final result = await Process.run(
      Platform.resolvedExecutable,
      <String>[
        path.join(
          Directory.current.path,
          'bin/fonix_prepare_flutter_assets.dart',
        ),
        '--target-os',
        'macos',
        '--architecture',
        'sparc',
        '--variant',
        'default',
        '--cache',
        temporary.path,
        '--output',
        path.join(temporary.path, 'assets'),
      ],
      workingDirectory: Directory.current.path,
      environment: <String, String>{
        ...Platform.environment,
        'DART_SUPPRESS_ANALYTICS': 'true',
      },
    );

    expect(result.exitCode, 64);
    expect(result.stderr, contains('no artifact for exact tuple'));
  });

  final archiveDirectory =
      Platform.environment['FONIX_TEST_MACOS_ORT_ARCHIVE_DIR'];
  test(
    'executable publishes exact locked assets twice',
    () async {
      final temporary = Directory.systemTemp.createTempSync(
        'fonix-asset-cli-exact-',
      );
      addTearDown(() => temporary.deleteSync(recursive: true));
      final output = Directory(path.join(temporary.path, 'assets'));
      final arguments = <String>[
        path.join(
          Directory.current.path,
          'bin/fonix_prepare_flutter_assets.dart',
        ),
        '--target-os',
        'macos',
        '--architecture',
        'arm64',
        '--variant',
        'default',
        '--cache',
        archiveDirectory!,
        '--output',
        output.path,
      ];
      for (var run = 0; run < 2; run++) {
        final result = await Process.run(
          Platform.resolvedExecutable,
          arguments,
          workingDirectory: Directory.current.path,
          environment: <String, String>{
            ...Platform.environment,
            'DART_SUPPRESS_ANALYTICS': 'true',
          },
        );
        expect(
          result.exitCode,
          0,
          reason: '${result.stdout}\n${result.stderr}',
        );
      }

      final manifest =
          jsonDecode(
                File(
                  path.join(output.path, 'fonix-native-artifact-manifest.json'),
                ).readAsStringSync(),
              )
              as Map<String, Object?>;
      expect(manifest['artifactId'], 'onnxruntime-1.27.1-macos-arm64-cpu');
      final notices = manifest['notices']! as List<Object?>;
      final notice = notices.cast<Map<String, Object?>>().singleWhere(
        (entry) => entry['id'] == 'ThirdPartyNotices',
      );
      final noticeBytes = File(
        path.join(output.path, 'ThirdPartyNotices.txt'),
      ).readAsBytesSync();
      expect(sha256.convert(noticeBytes).toString(), notice['sha256']);
      expect(noticeBytes.length, notice['sizeBytes']);
    },
    skip: archiveDirectory == null
        ? 'Set FONIX_TEST_MACOS_ORT_ARCHIVE_DIR to an exact offline cache.'
        : false,
  );
}
