part of 'runtime.dart';

/// Named values retained independently of their originating session.
///
/// The native result and every exposed value are owned by this object. Call
/// [dispose] deterministically when the outputs are no longer needed.
final class OrtRunResult extends _NativeOwner {
  factory OrtRunResult._({
    required OrtSession session,
    required Pointer<Void> handle,
    required List<String> outputNames,
    required Map<String, OrtValue> outputs,
    required OrtProviderRunEvidence? providerEvidence,
    required List<OrtProviderDiagnostics> providerDiagnostics,
    required OrtDiagnostics diagnostics,
  }) {
    final List<String> copiedNames = List<String>.unmodifiable(outputNames);
    final Map<String, OrtValue> copiedOutputs =
        Map<String, OrtValue>.unmodifiable(outputs);
    final List<_ExternalMemoryAccount> accounts =
        _retainRunResultMemoryAccounts(copiedOutputs);
    try {
      return OrtRunResult._owned(
        session: session,
        handle: handle,
        outputNames: copiedNames,
        outputs: copiedOutputs,
        providerEvidence: providerEvidence,
        providerDiagnostics: List<OrtProviderDiagnostics>.unmodifiable(
          providerDiagnostics,
        ),
        diagnostics: diagnostics,
        memoryAccounts: accounts,
      );
    } catch (_) {
      for (final _ExternalMemoryAccount account in accounts) {
        account.release();
      }
      rethrow;
    }
  }

  OrtRunResult._owned({
    required OrtSession session,
    required Pointer<Void> handle,
    required List<String> outputNames,
    required Map<String, OrtValue> outputs,
    required List<_ExternalMemoryAccount> memoryAccounts,
    required OrtProviderRunEvidence? providerEvidence,
    required List<OrtProviderDiagnostics> providerDiagnostics,
    required this.diagnostics,
  }) : _outputNames = outputNames,
       _outputs = outputs,
       _providerEvidence = providerEvidence,
       _providerDiagnostics = providerDiagnostics,
       _memoryAccounts = memoryAccounts,
       super(
         nativeApi: session._nativeApi,
         nativeHandle: handle,
         releaseAddress: session._nativeApi.runResultReleaseAddress,
         release: session._nativeApi.releaseRunResult,
         debugName: 'OrtRunResult',
       );

  final List<String> _outputNames;
  final Map<String, OrtValue> _outputs;
  final OrtProviderRunEvidence? _providerEvidence;
  final List<OrtProviderDiagnostics> _providerDiagnostics;

  /// Immutable, redacted diagnostics for this exact run.
  ///
  /// The snapshot remains usable after this result is disposed.
  final OrtDiagnostics diagnostics;
  List<_ExternalMemoryAccount>? _memoryAccounts;

  /// Validated assignment evidence for this exact run, when policy requested
  /// it. This immutable snapshot remains usable after disposal.
  OrtProviderRunEvidence? get providerEvidence => _providerEvidence;

  /// Immutable provider states for this exact run.
  ///
  /// `active` remains null when the policy did not request run profiling. This
  /// snapshot remains usable after disposal.
  List<OrtProviderDiagnostics> get providerDiagnostics => _providerDiagnostics;

  /// Output names in the exact requested order.
  List<String> get outputNames {
    _ensureOpen();
    return _outputNames;
  }

  /// An immutable name-to-value view of all requested outputs.
  ///
  /// These are borrowed objects owned by this result. Disposing this result
  /// disposes them. Use [retainValue] when an output must outlive the result.
  Map<String, OrtValue> get outputs {
    _ensureOpen();
    return _outputs;
  }

  /// Returns the borrowed value for [name].
  OrtValue value(String name) {
    _ensureOpen();
    final OrtValue? result = _outputs[name];
    if (result == null) {
      throw ArgumentError('The run result has no output with that name.');
    }
    return result;
  }

  /// Returns the borrowed numeric tensor for [name].
  OrtTensor tensor(String name) {
    final OrtValue result = value(name);
    if (result is! OrtTensor) {
      throw StateError('The named output is not a numeric tensor.');
    }
    return result;
  }

  /// Returns the borrowed string tensor for [name].
  OrtStringTensor stringTensor(String name) {
    final OrtValue result = value(name);
    if (result is! OrtStringTensor) {
      throw StateError('The named output is not a string tensor.');
    }
    return result;
  }

  /// Returns an independently retained value for [name].
  ///
  /// The caller owns the returned recursive value tree and must dispose it.
  OrtValue retainValue(String name) => _retainBorrowedValue(value(name));

  /// Returns an independently retained numeric tensor for [name].
  OrtTensor retainTensor(String name) {
    final OrtValue retained = retainValue(name);
    if (retained is OrtTensor) return retained;
    retained.dispose();
    throw StateError('The named output is not a numeric tensor.');
  }

  @override
  void _disposeDependents() {
    for (final OrtValue value in _outputs.values) {
      value.dispose();
    }
    final List<_ExternalMemoryAccount>? memoryAccounts = _memoryAccounts;
    _memoryAccounts = null;
    if (memoryAccounts != null) {
      for (final _ExternalMemoryAccount account in memoryAccounts) {
        account.release();
      }
    }
  }
}

OrtValue _retainBorrowedValue(OrtValue borrowed) {
  borrowed._ensureValueOpen();
  final Pointer<Void> handle = borrowed._nativeHandle;
  if (borrowed is OrtTensor) {
    final _ExternalMemoryAccount memoryAccount = borrowed
        ._retainMemoryAccount();
    var retainedHandle = false;
    try {
      borrowed._nativeApi.retainValue(handle);
      retainedHandle = true;
      return OrtTensor._(
        runtime: borrowed._runtime,
        handle: handle,
        info: borrowed._info,
        maximumCopyBytes: borrowed._maximumCopyBytes,
        memoryAccount: memoryAccount,
      );
    } catch (_) {
      if (retainedHandle) borrowed._nativeApi.releaseValue(handle);
      memoryAccount.release();
      rethrow;
    }
  }
  if (borrowed is OrtStringTensor) {
    final _ExternalMemoryAccount memoryAccount = borrowed
        ._retainMemoryAccount();
    var retainedHandle = false;
    try {
      borrowed._nativeApi.retainValue(handle);
      retainedHandle = true;
      return OrtStringTensor._(
        runtime: borrowed._runtime,
        handle: handle,
        info: borrowed._info,
        maximumElements: borrowed._maximumElements,
        maximumCopyBytes: borrowed._maximumCopyBytes,
        memoryAccount: memoryAccount,
      );
    } catch (_) {
      if (retainedHandle) borrowed._nativeApi.releaseValue(handle);
      memoryAccount.release();
      rethrow;
    }
  }

  var retainedHandle = false;
  final List<OrtValue> retainedChildren = <OrtValue>[];
  try {
    borrowed._nativeApi.retainValue(handle);
    retainedHandle = true;
    if (borrowed is OrtSequence) {
      retainedChildren.addAll(borrowed._elements.map(_retainBorrowedValue));
      return OrtSequence._(
        runtime: borrowed._runtime,
        handle: handle,
        type: borrowed._type,
        elements: retainedChildren,
      );
    }
    if (borrowed is OrtMap) {
      retainedChildren
        ..add(_retainBorrowedValue(borrowed.keys))
        ..add(_retainBorrowedValue(borrowed.values));
      return OrtMap._(
        runtime: borrowed._runtime,
        handle: handle,
        type: borrowed._type,
        keys: retainedChildren[0],
        values: retainedChildren[1],
      );
    }
    if (borrowed is OrtOptional) {
      if (borrowed.value case final OrtValue value) {
        retainedChildren.add(_retainBorrowedValue(value));
      }
      return OrtOptional._(
        runtime: borrowed._runtime,
        handle: handle,
        type: borrowed._type,
        value: retainedChildren.isEmpty ? null : retainedChildren.first,
      );
    }
    throw StateError('Unsupported owned value implementation.');
  } catch (_) {
    for (final OrtValue child in retainedChildren) {
      child.dispose();
    }
    if (retainedHandle) borrowed._nativeApi.releaseValue(handle);
    rethrow;
  }
}

List<_ExternalMemoryAccount> _retainRunResultMemoryAccounts(
  Map<String, OrtValue> outputs,
) {
  final List<_ExternalMemoryAccount> result = <_ExternalMemoryAccount>[];
  try {
    for (final OrtValue value in outputs.values) {
      _retainValueMemoryAccounts(value, result);
    }
    return result;
  } catch (_) {
    for (final _ExternalMemoryAccount account in result) {
      account.release();
    }
    rethrow;
  }
}

void _retainValueMemoryAccounts(
  OrtValue value,
  List<_ExternalMemoryAccount> output,
) {
  if (value is OrtTensor) {
    output.add(value._retainMemoryAccount());
    return;
  }
  if (value is OrtStringTensor) {
    output.add(value._retainMemoryAccount());
    return;
  }
  if (value is _CompositeValue) {
    for (final OrtValue child in value._children) {
      _retainValueMemoryAccounts(child, output);
    }
  }
}

OrtRunResult _runResultFromNative({
  required OrtSession session,
  required Pointer<Void> handle,
  required List<String> requestedOutputNames,
  required OrtProviderRunEvidence? providerEvidence,
  required List<OrtProviderDiagnostics> providerDiagnostics,
}) {
  final Map<String, OrtValue> outputs = <String, OrtValue>{};
  final _ValueDecodeBudget decodeBudget = _ValueDecodeBudget(
    limits: session._options.limits,
  );
  try {
    final int count = session._nativeApi.runResultCount(handle);
    if (count != requestedOutputNames.length ||
        count <= 0 ||
        count > _maximumMetadataItems) {
      throw OrtNativePackagingException(
        operation: 'run_result_count',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an incompatible output count.',
      );
    }

    for (var index = 0; index < count; index += 1) {
      final FonixNativeNamedValue namedValue;
      try {
        namedValue = session._nativeApi.getRunResultValue(handle, index);
      } on FonixNativeFailure catch (failure) {
        throw _translateNativeFailure(
          failure,
          runtimeSource: session._runtime._info.runtimeSource,
          requiredApi: OrtApiVersion.v27,
          privateValues: session._privateValues,
        );
      } on FormatException catch (cause) {
        throw OrtNativePackagingException(
          operation: 'run_result_value_name_decode',
          code: _nativeErrorPlatform,
          message: 'The native shim returned an incompatible output name.',
          cause: cause,
        );
      } on StateError catch (cause) {
        throw OrtNativePackagingException(
          operation: 'run_result_value_name_copy',
          code: _nativeErrorPlatform,
          message: 'The native shim returned an invalid output name.',
          cause: cause,
        );
      }

      final String expectedName = requestedOutputNames[index];
      if (namedValue.name != expectedName ||
          outputs.containsKey(namedValue.name)) {
        session._nativeApi.releaseValue(namedValue.value);
        throw OrtNativePackagingException(
          operation: 'run_result_name',
          code: _nativeErrorPlatform,
          message: 'The native shim returned incompatible named outputs.',
        );
      }

      final OrtValueInfo expectedOutput = session._metadata.outputs.singleWhere(
        (OrtValueInfo output) => output.name == namedValue.name,
      );
      try {
        outputs[namedValue.name] = _valueFromNative(
          runtime: session._runtime,
          handle: namedValue.value,
          expectedType: expectedOutput.type,
          limits: session._options.limits,
          budget: decodeBudget,
        );
      } on FonixNativeFailure catch (failure) {
        throw _translateNativeFailure(
          failure,
          runtimeSource: session._runtime._info.runtimeSource,
          requiredApi: OrtApiVersion.v27,
          privateValues: session._privateValues,
        );
      } on FormatException {
        throw OrtNativePackagingException(
          operation: 'run_result_value_decode',
          code: _nativeErrorPlatform,
          message: 'The native output conflicts with session metadata.',
        );
      } on StateError {
        throw OrtNativePackagingException(
          operation: 'run_result_value_copy',
          code: _nativeErrorPlatform,
          message: 'The native shim returned invalid output metadata.',
        );
      }
    }

    return OrtRunResult._(
      session: session,
      handle: handle,
      outputNames: requestedOutputNames,
      outputs: outputs,
      providerEvidence: providerEvidence,
      providerDiagnostics: providerDiagnostics,
      diagnostics: session._diagnosticsSnapshot(providerDiagnostics),
    );
  } catch (_) {
    for (final OrtValue value in outputs.values) {
      value.dispose();
    }
    session._nativeApi.releaseRunResult(handle);
    rethrow;
  }
}
