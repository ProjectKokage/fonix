import 'dart:io';

import 'package:flutter/widgets.dart';

import 'app.dart';
import 'src/android_smoke_channel.dart';
import 'src/fonix_inference_backend.dart';
import 'src/reference_smoke.dart';

const String _smokeEnvironmentKey = androidSmokeDefine;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (Platform.isAndroid && androidSmokeEnabled) {
    await _runAndroidSmoke();
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

Future<void> _runAndroidSmoke() async {
  const AndroidSmokeChannel channel = AndroidSmokeChannel();
  final ReferenceSmokeReceipt receipt;
  try {
    receipt = await runReferenceSmoke(FonixInferenceBackend());
  } on Object catch (error) {
    await channel.completeFailed(error);
    return;
  }
  await channel.completePassed(receipt);
}
