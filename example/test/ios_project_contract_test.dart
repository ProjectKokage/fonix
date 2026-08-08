import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

const String _flutterRevision = 'bd1e75d918605c91b411e8789fb911e6c9a84534';
const String _assetRoot = 'ios/Runner/Assets.xcassets';
const Map<String, String> _pinnedAssetMetadataSha256 = <String, String>{
  'AppIcon.appiconset/Contents.json':
      'cd841cae92e48a8d48c79a6b634e7fb1f88fb97c924e24736b3bc3824ede3975',
  'LaunchImage.imageset/Contents.json':
      '3f762eb75010d8d2197b0378ac464bd6a39f7365d44f2cf4dfdaecd3139a7153',
  'LaunchImage.imageset/README.md':
      'e68c9ff1fa9c66859d05168a38843dabe2907e207db427f02921c43451563c41',
};
const Map<String, String> _pinnedIosImageSha256 = <String, String>{
  'AppIcon.appiconset/Icon-App-1024x1024@1x.png':
      '7770183009e914112de7d8ef1d235a6a30c5834424858e0d2f8253f6b8d31926',
  'AppIcon.appiconset/Icon-App-20x20@1x.png':
      'cab10a0d391ec5bc09ef50ce49e8ad401cee7ef03707ec0923a222c5c2b3d212',
  'AppIcon.appiconset/Icon-App-20x20@2x.png':
      'b9ad02cf6576a04d1b6806ac02a2431481b448dd0c2e505ce25842d1f7c4730b',
  'AppIcon.appiconset/Icon-App-20x20@3x.png':
      'c6e6d3b215ae744a9c391f4c4d44157eff5e739d6ad6c39f9bfa5df66dddd267',
  'AppIcon.appiconset/Icon-App-29x29@1x.png':
      '5dee24dc104ac76dc162e42ae0beb163d426bf365562ee28ba7b3ad368559a60',
  'AppIcon.appiconset/Icon-App-29x29@2x.png':
      'a9b21eb6f4271385655a8771f76e29eef8c1107d7879cbcfc567e6619d1f716a',
  'AppIcon.appiconset/Icon-App-29x29@3x.png':
      'e677d701ffe4af7bc2935098d6b3984cc9ab7ace573e6900955a5535b12410cf',
  'AppIcon.appiconset/Icon-App-40x40@1x.png':
      'b9ad02cf6576a04d1b6806ac02a2431481b448dd0c2e505ce25842d1f7c4730b',
  'AppIcon.appiconset/Icon-App-40x40@2x.png':
      '7c61c42fc7b657d9cf314d32a4ec458f0647c3aaf360be1b9377857266ec2499',
  'AppIcon.appiconset/Icon-App-40x40@3x.png':
      '19be171481dc71a0b2803ebcd01dd8b0c5fd5778dee34c0a3cabc948c225f24e',
  'AppIcon.appiconset/Icon-App-60x60@2x.png':
      '19be171481dc71a0b2803ebcd01dd8b0c5fd5778dee34c0a3cabc948c225f24e',
  'AppIcon.appiconset/Icon-App-60x60@3x.png':
      '4209a49e44a92ec40a327d3455eb1b1c153ee83d75de1c2be0a12ab18b2ff9de',
  'AppIcon.appiconset/Icon-App-76x76@1x.png':
      '836c918cb613249eba0483a6b02fa3df3c1c0a89a315ee4d3b88509b83c7ab73',
  'AppIcon.appiconset/Icon-App-76x76@2x.png':
      '41c7d42f6e61f8fe7f30b1ffa2256aecbc9682be06d18c4a3062043e1a2e547c',
  'AppIcon.appiconset/Icon-App-83.5x83.5@2x.png':
      '5d7e5bdf01b93802bc973345b3a78c038907147625035952a08a115a563b7f81',
  'LaunchImage.imageset/LaunchImage.png':
      '93ae7d494fad0fb30cbf3ae746a39c4bc7a0f8bbf87fbb587a3f3c01f3c5ce20',
  'LaunchImage.imageset/LaunchImage@2x.png':
      '93ae7d494fad0fb30cbf3ae746a39c4bc7a0f8bbf87fbb587a3f3c01f3c5ce20',
  'LaunchImage.imageset/LaunchImage@3x.png':
      '93ae7d494fad0fb30cbf3ae746a39c4bc7a0f8bbf87fbb587a3f3c01f3c5ce20',
};
const List<Map<String, String>> _pinnedAppIconBindings = <Map<String, String>>[
  <String, String>{
    'size': '20x20',
    'idiom': 'iphone',
    'filename': 'Icon-App-20x20@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '20x20',
    'idiom': 'iphone',
    'filename': 'Icon-App-20x20@3x.png',
    'scale': '3x',
  },
  <String, String>{
    'size': '29x29',
    'idiom': 'iphone',
    'filename': 'Icon-App-29x29@1x.png',
    'scale': '1x',
  },
  <String, String>{
    'size': '29x29',
    'idiom': 'iphone',
    'filename': 'Icon-App-29x29@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '29x29',
    'idiom': 'iphone',
    'filename': 'Icon-App-29x29@3x.png',
    'scale': '3x',
  },
  <String, String>{
    'size': '40x40',
    'idiom': 'iphone',
    'filename': 'Icon-App-40x40@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '40x40',
    'idiom': 'iphone',
    'filename': 'Icon-App-40x40@3x.png',
    'scale': '3x',
  },
  <String, String>{
    'size': '60x60',
    'idiom': 'iphone',
    'filename': 'Icon-App-60x60@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '60x60',
    'idiom': 'iphone',
    'filename': 'Icon-App-60x60@3x.png',
    'scale': '3x',
  },
  <String, String>{
    'size': '20x20',
    'idiom': 'ipad',
    'filename': 'Icon-App-20x20@1x.png',
    'scale': '1x',
  },
  <String, String>{
    'size': '20x20',
    'idiom': 'ipad',
    'filename': 'Icon-App-20x20@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '29x29',
    'idiom': 'ipad',
    'filename': 'Icon-App-29x29@1x.png',
    'scale': '1x',
  },
  <String, String>{
    'size': '29x29',
    'idiom': 'ipad',
    'filename': 'Icon-App-29x29@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '40x40',
    'idiom': 'ipad',
    'filename': 'Icon-App-40x40@1x.png',
    'scale': '1x',
  },
  <String, String>{
    'size': '40x40',
    'idiom': 'ipad',
    'filename': 'Icon-App-40x40@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '76x76',
    'idiom': 'ipad',
    'filename': 'Icon-App-76x76@1x.png',
    'scale': '1x',
  },
  <String, String>{
    'size': '76x76',
    'idiom': 'ipad',
    'filename': 'Icon-App-76x76@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '83.5x83.5',
    'idiom': 'ipad',
    'filename': 'Icon-App-83.5x83.5@2x.png',
    'scale': '2x',
  },
  <String, String>{
    'size': '1024x1024',
    'idiom': 'ios-marketing',
    'filename': 'Icon-App-1024x1024@1x.png',
    'scale': '1x',
  },
];
const List<Map<String, String>> _pinnedLaunchImageBindings =
    <Map<String, String>>[
      <String, String>{
        'idiom': 'universal',
        'filename': 'LaunchImage.png',
        'scale': '1x',
      },
      <String, String>{
        'idiom': 'universal',
        'filename': 'LaunchImage@2x.png',
        'scale': '2x',
      },
      <String, String>{
        'idiom': 'universal',
        'filename': 'LaunchImage@3x.png',
        'scale': '3x',
      },
    ];

void main() {
  test('pins the iOS scaffold to Flutter, arm64, and iOS 15.1', () {
    final String metadata = File('.metadata').readAsStringSync();
    expect(metadata, contains('revision: "$_flutterRevision"'));
    expect(
      metadata,
      contains(
        '    - platform: ios\n'
        '      create_revision: $_flutterRevision\n'
        '      base_revision: $_flutterRevision\n',
      ),
    );

    final String project = File(
      'ios/Runner.xcodeproj/project.pbxproj',
    ).readAsStringSync();
    expect(
      'IPHONEOS_DEPLOYMENT_TARGET = 15.1;'.allMatches(project),
      hasLength(3),
    );
    expect('ARCHS = arm64;'.allMatches(project), hasLength(3));
    expect(
      RegExp(
        r'^[ \t]*"?ARCHS(?:\[[^\]]+\])?"?[ \t]*=',
        multiLine: true,
      ).allMatches(project),
      hasLength(3),
    );
    expect(
      RegExp(
        r'^[ \t]*"?IPHONEOS_DEPLOYMENT_TARGET(?:\[[^\]]+\])?"?[ \t]*=',
        multiLine: true,
      ).allMatches(project),
      hasLength(3),
    );
    for (final String configuration in <String>[
      'ios/Flutter/Debug.xcconfig',
      'ios/Flutter/Release.xcconfig',
    ]) {
      expect(
        File(configuration).readAsStringSync(),
        '#include "Generated.xcconfig"\n',
        reason: configuration,
      );
    }
    expect(
      'lastKnownFileType = text.xcconfig;'.allMatches(project),
      hasLength(3),
    );
    expect(
      RegExp(
            r'^\s*baseConfigurationReference = .+xcconfig \*/;$',
            multiLine: true,
          )
          .allMatches(project)
          .map((RegExpMatch match) => match.group(0)!.trim())
          .toList(),
      <String>[
        'baseConfigurationReference = '
            '7AFA3C8E1D35360C0083082E /* Release.xcconfig */;',
        'baseConfigurationReference = '
            '9740EEB21CF90195004384FC /* Debug.xcconfig */;',
        'baseConfigurationReference = '
            '7AFA3C8E1D35360C0083082E /* Release.xcconfig */;',
      ],
    );
    expect(project, isNot(contains('DEVELOPMENT_TEAM')));
    expect(project, isNot(contains('PROVISIONING_PROFILE')));
    expect(project, contains('isa = XCLocalSwiftPackageReference;'));
    expect(
      project,
      contains(
        'relativePath = Flutter/ephemeral/Packages/'
        'FlutterGeneratedPluginSwiftPackage;',
      ),
    );
  });

  test('keeps the iOS app permission-free with pinned scaffold assets', () {
    final String info = File('ios/Runner/Info.plist').readAsStringSync();
    expect(info, contains('<string>Fonix Reference</string>'));
    expect(info, isNot(contains('UsageDescription')));
    expect(info, isNot(contains('UIBackgroundModes')));

    final Directory assetRoot = Directory(_assetRoot);
    final Map<String, File> files = <String, File>{};
    for (final FileSystemEntity entity in assetRoot.listSync(
      recursive: true,
      followLinks: false,
    )) {
      final FileSystemEntityType type = FileSystemEntity.typeSync(
        entity.path,
        followLinks: false,
      );
      expect(
        type,
        isIn(<FileSystemEntityType>[
          FileSystemEntityType.directory,
          FileSystemEntityType.file,
        ]),
        reason: entity.path,
      );
      if (type == FileSystemEntityType.file) {
        final String relative = entity.path
            .substring(assetRoot.path.length + 1)
            .replaceAll(Platform.pathSeparator, '/');
        files[relative] = File(entity.path);
      }
    }

    expect(files.keys.toSet(), <String>{
      ..._pinnedAssetMetadataSha256.keys,
      ..._pinnedIosImageSha256.keys,
    });
    for (final MapEntry<String, String> expected in <String, String>{
      ..._pinnedAssetMetadataSha256,
      ..._pinnedIosImageSha256,
    }.entries) {
      expect(
        sha256.convert(files[expected.key]!.readAsBytesSync()).toString(),
        expected.value,
        reason: expected.key,
      );
    }
    expect(
      _catalogBindings(files['AppIcon.appiconset/Contents.json']!),
      _pinnedAppIconBindings,
    );
    expect(
      _catalogBindings(files['LaunchImage.imageset/Contents.json']!),
      _pinnedLaunchImageBindings,
    );
  });

  test('normalizes only Debug simulator packaging after Thin Binary', () {
    final String project = File(
      'ios/Runner.xcodeproj/project.pbxproj',
    ).readAsStringSync();

    expect('ENABLE_DEBUG_DYLIB = NO;'.allMatches(project), hasLength(1));
    expect(
      RegExp(
        r'^[ \t]*"?ENABLE_DEBUG_DYLIB(?:\[[^\]]+\])?"?[ \t]*=',
        multiLine: true,
      ).allMatches(project),
      hasLength(1),
    );
    const String runnerBuildPhaseOrder =
        '\t\t\tbuildPhases = (\n'
        '\t\t\t\t9740EEB61CF901F6004384FC /* Run Script */,\n'
        '\t\t\t\t97C146EA1CF9000F007C117D /* Sources */,\n'
        '\t\t\t\t97C146EB1CF9000F007C117D /* Frameworks */,\n'
        '\t\t\t\t97C146EC1CF9000F007C117D /* Resources */,\n'
        '\t\t\t\t9705A1C41CF9048500538489 /* Embed Frameworks */,\n'
        '\t\t\t\t3B06AD1E1E4923F5004D2608 /* Thin Binary */,\n'
        '\t\t\t\tF0A1B2C3D4E5F60718293A4B '
        '/* Normalize Debug Simulator Rpath */,\n'
        '\t\t\t);';
    expect(runnerBuildPhaseOrder.allMatches(project), hasLength(1));
    expect(
      project,
      contains(
        '3B06AD1E1E4923F5004D2608 /* Thin Binary */,\n'
        '\t\t\t\tF0A1B2C3D4E5F60718293A4B '
        '/* Normalize Debug Simulator Rpath */',
      ),
    );
    expect(
      project,
      contains(
        'if [ \\"\$CONFIGURATION\\" != \\"Debug\\" ] || '
        '[ \\"\$PLATFORM_NAME\\" != \\"iphonesimulator\\" ]; then',
      ),
    );
    expect(
      project,
      contains('expected_rpath=\\"\${TARGET_BUILD_DIR}/PackageFrameworks\\"'),
    );
    expect(
      project,
      contains('binary=\\"\${TARGET_BUILD_DIR}/\${EXECUTABLE_PATH}\\"'),
    );
    expect(project, contains('if [ ! -f \\"\$binary\\" ]; then'));
    expect(project, contains('/usr/bin/otool -l \\"\$binary\\"'));
    expect(project, contains('rpath_count() {'));
    expect(
      project,
      contains('expected_count=\\"\$(rpath_count \\"\$expected_rpath\\")\\"'),
    );
    expect(project, contains('  0)\\n    ;;\\n  1)'));
    expect(
      project,
      contains(
        '/usr/bin/install_name_tool -delete_rpath '
        '\\"\$expected_rpath\\" \\"\$binary\\"',
      ),
    );
    expect(project, contains('total_count=\\"\$(rpath_count \\"\\")\\"'));
    expect(
      project,
      contains('swift_count=\\"\$(rpath_count \\"/usr/lib/swift\\")\\"'),
    );
    expect(
      project,
      contains(
        'frameworks_count=\\"\$(rpath_count '
        '\\"@executable_path/Frameworks\\")\\"',
      ),
    );
    final String phaseBlock = _projectObjectBlock(
      project,
      'F0A1B2C3D4E5F60718293A4B /* Normalize Debug Simulator Rpath */',
    );
    expect(
      sha256.convert(utf8.encode('$phaseBlock\n')).toString(),
      '9307eae6d1a0b83d2ed30d00b9b04f00be5be1e1b17cdb371acc3760e0fef3c6',
    );
  });

  test('iOS delegate exposes one closed launch activation channel', () {
    final String source = File(
      'ios/Runner/AppDelegate.swift',
    ).readAsStringSync();
    expect(source, contains('FlutterImplicitEngineDelegate'));
    expect(source, contains('didInitializeImplicitFlutterEngine'));
    expect(
      source,
      contains(
        'GeneratedPluginRegistrant.register(with: '
        'engineBridge.pluginRegistry)',
      ),
    );
    expect(source, contains('"dev.fonix.reference/launch"'));
    expect(source, contains('"readSmokeActivation"'));
    expect(source, contains('"FONIX_REFERENCE_SMOKE"'));
    expect(source, contains('"FONIX_REFERENCE_CHALLENGE"'));
    expect(source, contains('engineBridge.applicationRegistrar.messenger()'));
    expect(
      source,
      contains('guard call.method == Self.referenceLaunchMethod else'),
    );
    expect(source, contains('result(FlutterMethodNotImplemented)'));
    expect(source, contains('guard call.arguments == nil else'));
    expect(
      source,
      contains(
        'private lazy var referenceLaunchActivation = '
        'Self.readReferenceLaunchActivation()',
      ),
    );
    expect(source, contains('switch self.referenceLaunchActivation'));
    expect(source, contains('case .absent:'));
    expect(source, contains('case .active(let challenge):'));
    expect(source, contains('case .invalid:'));
    expect(source, isNot(contains('referenceLaunchActivationConsumed')));
    expect(source, isNot(contains('duplicate-reference-launch-read')));
    expect(
      source,
      contains('if smoke == nil && challenge == nil {\n      return .absent'),
    );
    expect(source, contains('smoke == "1"'));
    expect(source, contains('Self.isLowercaseHexChallenge(challenge)'));
    expect(source, contains('bytes.count == 64'));
    expect(source, contains('byte >= 48 && byte <= 57'));
    expect(source, contains('byte >= 97 && byte <= 102'));
    expect(
      source,
      contains('result(["schemaVersion": 1, "challenge": challenge])'),
    );
    expect(source, isNot(contains('result(environment)')));
  });

  test(
    'iOS packaged smoke is resident and desktop exit stays desktop-only',
    () {
      final String source = File('lib/main.dart').readAsStringSync();
      expect(source, contains('if (Platform.isIOS)'));
      expect(
        source,
        contains('challenge = await readIosResidentReferenceChallenge()'),
      );
      expect(
        source,
        contains('_runResidentPackagedSmoke(challenge, pid).catchError'),
      );
      expect(source, contains('unawaited('));
      expect(source, isNot(contains('.ignore()')));
      expect(source, contains('residentReferenceActivationFailureDiagnostic'));
      expect(source, contains('residentReferencePublicationFailureDiagnostic'));
      expect(source, contains('runResidentReferenceSmoke('));
      expect(source, contains('publishResidentReferenceLine'));
      expect(source, contains('runApp(const SizedBox.shrink())'));
      expect(
        source,
        contains(
          'if ((Platform.isMacOS || Platform.isLinux) &&\n'
          '      desktopReferenceSmokeEnabled(',
        ),
      );
      expect(source, contains('isMacOS: Platform.isMacOS'));
      expect(source, contains('isLinux: Platform.isLinux'));
      expect(source, contains('environment: Platform.environment'));
      expect(source, contains('desktopCpuBenchmarkEnabled('));
      expect('Platform.environment'.allMatches(source), hasLength(2));
      expect('exit(status);'.allMatches(source), hasLength(2));
    },
  );
}

String _projectObjectBlock(String project, String objectName) {
  final String startMarker = '\t\t$objectName = {';
  final int start = project.indexOf(startMarker);
  expect(start, isNonNegative, reason: objectName);
  const String endMarker = '\n\t\t};';
  final int end = project.indexOf(endMarker, start);
  expect(end, isNonNegative, reason: objectName);
  return project.substring(start, end + endMarker.length);
}

List<Map<String, String>> _catalogBindings(File file) {
  final Map<String, Object?> value =
      jsonDecode(file.readAsStringSync()) as Map<String, Object?>;
  final List<Object?> images = value['images']! as List<Object?>;
  return <Map<String, String>>[
    for (final Object? image in images)
      (image! as Map<String, Object?>).map(
        (String key, Object? value) =>
            MapEntry<String, String>(key, value! as String),
      ),
  ];
}
