import 'dart:io';

import 'package:flutter/widgets.dart';

import 'app.dart';
import 'src/android_smoke_channel.dart';
import 'src/android_xnnpack_qualification.dart';
import 'src/fonix_inference_backend.dart';
import 'src/reference_smoke.dart';

const String _smokeEnvironmentKey = androidSmokeDefine;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (Platform.isAndroid && androidSmokeEnabled) {
    await _runAndroidSmoke(AndroidSmokeProfile.parse(androidSmokeProfileValue));
    return;
  }
  if (Platform.environment[_smokeEnvironmentKey] == '1') {
    final int status = await _runPackagedSmoke();
    await stdout.flush();
    await stderr.flush();
    exit(status);
  }
  runApp(FonixReferenceApp(createBackend: FonixInferenceBackend.new));
}

Future<int> _runPackagedSmoke() async {
  try {
    final ReferenceSmokeReceipt receipt = await runReferenceSmoke(
      FonixInferenceBackend(),
    );
    stdout.writeln('$referenceSmokeReceiptPrefix${receipt.toJsonString()}');
    return 0;
  } on Object catch (error) {
    stderr.writeln('Fonix reference smoke failed (${error.runtimeType}).');
    return 1;
  }
}

Future<void> _runAndroidSmoke(AndroidSmokeProfile profile) async {
  const AndroidSmokeChannel channel = AndroidSmokeChannel();
  final String receipt;
  try {
    receipt = switch (profile) {
      AndroidSmokeProfile.cpu => (await runReferenceSmoke(
        FonixInferenceBackend(),
      )).toJsonString(),
      AndroidSmokeProfile.xnnpack =>
        (await runAndroidXnnpackQualification()).toJsonString(),
    };
  } on Object catch (error) {
    await channel.completeFailed(profile: profile, error: error);
    return;
  }
  await channel.completePassed(profile: profile, receipt: receipt);
}
