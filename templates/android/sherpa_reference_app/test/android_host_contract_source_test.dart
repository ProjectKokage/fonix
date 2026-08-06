import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('MainActivity retains the closed host boundary checks', () {
    final String source = File(
      'android/app/src/main/kotlin/dev/fonix/sherpa_reference/MainActivity.kt',
    ).readAsStringSync();

    expect(source, contains('payloadSchema !is Int'));
    expect(source, contains('payloadResult !is String'));
    expect(source, contains('payloadLoadOrder !is String'));
    expect(source, contains('payloadChallengeSha256 !is String'));
    expect(source, contains('if (reason !is String) return null'));
    expect(source, contains('extras.keySet() != LAUNCH_EXTRA_KEYS'));
    expect(source, contains('const val MAX_CHALLENGE_BYTES = 1024'));
    expect(source, contains('const val MAX_CHALLENGE_BASE64_BYTES = 1368'));
    expect(source, contains('The app does not self-exit.'));
    expect(source, isNot(contains('android.os.Process.myUid')));
    expect(source, isNot(contains('android.os.Process.myPid')));
  });

  test('Release packaging preserves every provenance-bound native input', () {
    final String source = File(
      'android/app/build.gradle.kts',
    ).readAsStringSync();

    for (final String library in <String>[
      'libonnxruntime.so',
      'libsherpa-onnx-c-api.so',
      'libsherpa-onnx-cxx-api.so',
      'libfonix_shim.so',
    ]) {
      expect(source, contains('"**/$library"'));
    }
    expect(source, contains('keepDebugSymbols += setOf('));
    expect(source, isNot(contains('pickFirst')));
  });

  test('Android Gradle wrapper is complete before Flutter builds', () {
    final String unixLauncher = File('android/gradlew').readAsStringSync();
    final String windowsLauncher = File(
      'android/gradlew.bat',
    ).readAsStringSync();

    expect(unixLauncher, contains('org.gradle.wrapper.GradleWrapperMain'));
    expect(windowsLauncher, contains('org.gradle.wrapper.GradleWrapperMain'));
    expect(
      File('android/gradle/wrapper/gradle-wrapper.jar').lengthSync(),
      greaterThan(0),
    );
    expect(
      File(
        'android/gradle/wrapper/gradle-wrapper.properties',
      ).readAsStringSync(),
      contains('gradle-9.3.1-all.zip'),
    );
  });
}
