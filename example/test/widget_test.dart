import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/app.dart';

import 'fake_inference_backend.dart';

void main() {
  testWidgets('renders startup, inference, and provider evidence', (
    WidgetTester tester,
  ) async {
    tester.view.physicalSize = const Size(800, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final FakeBackendFactory factory = FakeBackendFactory();

    await tester.pumpWidget(FonixReferenceApp(createBackend: factory.call));
    final FakeInferenceBackend backend = factory.backends.single;
    expect(find.byKey(const Key('status-text')), findsOneWidget);
    expect(find.text('Starting worker…'), findsOneWidget);
    backend.startup.complete(fakeStartupReceipt());
    await tester.pump();
    expect(find.text('Ready'), findsOneWidget);

    await tester.tap(find.byKey(const Key('run-button')));
    await tester.pump();
    expect(find.text('Inference running…'), findsOneWidget);
    backend.runs.single.complete(fakeRunReceipt());
    await tester.pump();

    expect(find.text('Run completed'), findsOneWidget);
    expect(find.text('[1, 4]\n[9, 16]\n[25, 36]'), findsOneWidget);
    expect(
      find.text('Active: cpu · full CPU assignment: true'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
  });

  testWidgets('fits a compact viewport without overflow', (
    WidgetTester tester,
  ) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final FakeBackendFactory factory = FakeBackendFactory();

    await tester.pumpWidget(FonixReferenceApp(createBackend: factory.call));
    factory.backends.single.startup.complete(fakeStartupReceipt());
    await tester.pump();
    expect(find.byKey(const Key('run-button')), findsOneWidget);
    expect(find.byKey(const Key('diagnostics-card')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
  });
}
