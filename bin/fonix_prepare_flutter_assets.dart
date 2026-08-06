import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import 'package:path/path.dart' as path;

import '../hook/src/native_artifact_resolver.dart';

const String _manifestName = 'fonix-native-artifact-manifest.json';
const String _noticeName = 'ThirdPartyNotices.txt';

Future<void> main(List<String> arguments) async {
  try {
    if (arguments.contains('--help')) {
      stdout.write(_usage);
      return;
    }
    final options = FonixFlutterAssetOptions.parse(arguments);
    validateFonixFlutterAssetOutputDirectory(options.outputDirectory);
    final packageRoot = options.packageRoot ?? await resolveFonixPackageRoot();
    final stagingRoot = Directory.systemTemp.createTempSync(
      'fonix-flutter-assets-',
    );
    try {
      final staged = await const NativeArtifactResolver().stage(
        packageRoot: packageRoot,
        stagingDirectory: Directory(path.join(stagingRoot.path, 'staged')),
        target: NativeArtifactTarget(
          operatingSystem: options.operatingSystem,
          architecture: options.architecture,
          variant: options.variant,
        ),
        cacheDirectory: options.cacheDirectory,
        mirrorDirectory: options.mirrorDirectory,
      );
      final noticeMatches = staged.noticeFiles
          .where((file) => path.basename(file.path) == _noticeName)
          .toList(growable: false);
      if (noticeMatches.length != 1) {
        throw const FormatException(
          'The selected artifact did not stage exactly one canonical '
          'ThirdPartyNotices.txt.',
        );
      }
      options.outputDirectory.createSync(recursive: true);
      publishFonixFlutterAsset(
        staged.manifestFile,
        File(path.join(options.outputDirectory.path, _manifestName)),
      );
      publishFonixFlutterAsset(
        noticeMatches.single,
        File(path.join(options.outputDirectory.path, _noticeName)),
      );
      stdout.writeln(
        jsonEncode(<String, Object?>{
          'artifactId': staged.identity.artifactId,
          'target': <String, String>{
            'os': staged.identity.operatingSystem,
            'architecture': staged.identity.architecture,
            'variant': staged.identity.variant,
          },
          'minimumOs': staged.identity.minimumOs,
          'sourceSha256': staged.identity.sourceSha256,
          'thirdPartyNoticesSha256': staged.identity.thirdPartyNoticesSha256,
          'outputDirectory': options.outputDirectory.absolute.path,
        }),
      );
    } finally {
      if (stagingRoot.existsSync()) {
        stagingRoot.deleteSync(recursive: true);
      }
    }
  } on Object catch (error) {
    stderr.writeln('fonix_prepare_flutter_assets: $error');
    stderr.write(_usage);
    exitCode = 64;
  }
}

Future<Directory> resolveFonixPackageRoot() async {
  final library = await Isolate.resolvePackageUri(
    Uri.parse('package:fonix/fonix.dart'),
  );
  if (library == null || library.scheme != 'file') {
    throw const FormatException(
      'Could not resolve the installed fonix package root.',
    );
  }
  return File.fromUri(library).parent.parent;
}

void validateFonixFlutterAssetOutputDirectory(Directory directory) {
  if (!path.isAbsolute(directory.path)) {
    throw const FormatException('--output must be an absolute directory.');
  }
  final type = FileSystemEntity.typeSync(directory.path, followLinks: false);
  if (type == FileSystemEntityType.link) {
    throw const FormatException('--output must not be a symbolic link.');
  }
  if (type != FileSystemEntityType.notFound &&
      type != FileSystemEntityType.directory) {
    throw const FormatException('--output must name a directory.');
  }
}

void publishFonixFlutterAsset(
  File source,
  File destination, {
  int? publisherProcessId,
}) {
  if (FileSystemEntity.typeSync(source.path, followLinks: false) !=
      FileSystemEntityType.file) {
    throw FormatException('Staged input is not a regular file: ${source.path}');
  }
  final destinationType = FileSystemEntity.typeSync(
    destination.path,
    followLinks: false,
  );
  if (destinationType != FileSystemEntityType.notFound &&
      destinationType != FileSystemEntityType.file) {
    throw FormatException(
      'Refusing to replace a non-regular Flutter asset: ${destination.path}',
    );
  }
  final temporary = File(
    '${destination.path}.tmp-${publisherProcessId ?? pid}',
  );
  if (FileSystemEntity.typeSync(temporary.path, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw FormatException(
      'Refusing to replace an existing temporary asset: ${temporary.path}',
    );
  }
  try {
    temporary.writeAsBytesSync(source.readAsBytesSync(), flush: true);
    temporary.renameSync(destination.path);
  } finally {
    if (temporary.existsSync()) {
      temporary.deleteSync();
    }
  }
}

final class FonixFlutterAssetOptions {
  const FonixFlutterAssetOptions({
    required this.packageRoot,
    required this.outputDirectory,
    required this.operatingSystem,
    required this.architecture,
    required this.variant,
    required this.cacheDirectory,
    required this.mirrorDirectory,
  });

  factory FonixFlutterAssetOptions.parse(List<String> arguments) {
    if (arguments.length.isOdd) {
      throw const FormatException('Every option requires one value.');
    }
    final values = <String, String>{};
    for (var index = 0; index < arguments.length; index += 2) {
      final key = arguments[index];
      if (!_allowedOptions.contains(key) || values.containsKey(key)) {
        throw FormatException('Unknown or duplicate option: $key');
      }
      final value = arguments[index + 1];
      if (value.isEmpty) {
        throw FormatException('$key must not be empty.');
      }
      values[key] = value;
    }
    String required(String key) {
      final value = values[key];
      if (value == null) {
        throw FormatException('Missing required option: $key');
      }
      return value;
    }

    final packageRootValue = values['--package-root'];
    final packageRoot = packageRootValue == null
        ? null
        : Directory(packageRootValue);
    final output = required('--output');
    final operatingSystem = required('--target-os');
    final architecture = required('--architecture');
    final variant = required('--variant');
    final cache = values['--cache'];
    final mirror = values['--mirror'];
    if (cache == null && mirror == null) {
      throw const FormatException('--cache or --mirror is required.');
    }
    for (final entry in <(String, String)>[
      if (packageRootValue != null) ('--package-root', packageRootValue),
      ('--output', output),
      if (cache != null) ('--cache', cache),
      if (mirror != null) ('--mirror', mirror),
    ]) {
      if (!path.isAbsolute(entry.$2)) {
        throw FormatException('${entry.$1} must be an absolute path.');
      }
    }
    return FonixFlutterAssetOptions(
      packageRoot: packageRoot,
      outputDirectory: Directory(output),
      operatingSystem: operatingSystem,
      architecture: architecture,
      variant: variant,
      cacheDirectory: cache == null ? null : Directory(cache),
      mirrorDirectory: mirror == null ? null : Directory(mirror),
    );
  }

  final Directory? packageRoot;
  final Directory outputDirectory;
  final String operatingSystem;
  final String architecture;
  final String variant;
  final Directory? cacheDirectory;
  final Directory? mirrorDirectory;
}

const Set<String> _allowedOptions = <String>{
  '--package-root',
  '--output',
  '--target-os',
  '--architecture',
  '--variant',
  '--cache',
  '--mirror',
};

const String _usage =
    '''
Usage: dart run fonix:fonix_prepare_flutter_assets
  --target-os <ios|macos|android|linux|windows>
  --architecture <locked architecture>
  --variant <device|simulator|default>
  [--package-root <absolute fonix package root>]
  --cache <absolute offline archive directory>  (or --mirror)
  --output <absolute app asset directory>

The output directory must be declared by the consuming Flutter app as assets.
The command writes only $_manifestName and $_noticeName.
''';
