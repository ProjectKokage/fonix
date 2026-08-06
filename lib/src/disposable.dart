/// A native resource with deterministic, idempotent disposal.
abstract interface class Disposable {
  /// Whether this Dart owner has relinquished its native reference.
  bool get isDisposed;

  /// Relinquishes this owner's native reference.
  ///
  /// Calling this method more than once has no effect.
  void dispose();
}
