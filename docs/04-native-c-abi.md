# 4. Native C ABI

## 4.1 Purpose

The native shim presents a compact, stable C ABI to Dart and isolates ONNX
Runtime version, provider, and platform details. The definitive ABI lives in
[`../src/dort.h`](../src/dort.h); generated Dart bindings are derived from that
same header. Fonix does not maintain a second template copy that can drift from
the implemented ABI.

## 4.2 ABI rules

- C linkage and C-compatible types only.
- Exported functions use the `dort_` prefix.
- Opaque handle types for runtimes, sessions, values, buffers, results, and statuses.
- Explicit-width integer types for enums/flags/codes.
- `size_t` only for byte counts/array lengths where Dart checks the platform range.
- Every extensible struct begins with `struct_size` and, where relevant, `abi_version`.
- Callers zero-initialize structs, set `struct_size`, and set reserved fields to zero. The callee rejects a size smaller than the required prefix, reads only fields present in that size, and ignores a larger unknown tail.
- Input strings, arrays, and config structs are borrowed only for the duration of the call unless an API explicitly says it retains/copies them. Session-options creation must copy everything it needs.
- Inputs are immutable unless named `inout`.
- Every out pointer is initialized to null/zero before work begins.
- Native strings are UTF-8 with documented ownership and lengths.
- No thread-local “last error” as the sole error channel.
- No callbacks into Dart from ORT threads in the baseline ABI.
- Build with hidden visibility and export only the version script/definition list.

## 4.3 Status convention

Mirror the useful part of ORT's convention:

```c
dort_status_t* dort_session_create(
    dort_runtime_t* runtime,
    const dort_session_config_t* config,
    dort_session_t** out_session);
```

- Return `NULL` on success.
- Return an owned `dort_status_t*` on failure.
- Caller reads code/message through accessors and releases it with `dort_status_release`.
- No other output is valid on failure unless explicitly documented.

The status copies the ORT message before releasing `OrtStatus`. It must survive independently of ORT object lifetime.

An ORT error does not erase a non-null owner returned through another output.
The shim releases such partial provider-discovery or model-metadata owners when
their pointer/count contract is bounded and internally consistent. It never
walks an ORT-returned pointer array until the reported count is representable
and within the ABI limit. If a corrupt metadata count is oversized, cleanup may
free only the separately known outer allocation; it must not index the claimed
entries. An invalid provider pointer/count pair is not passed back to ORT's
counted release API.

Suggested domains:

- shim validation;
- runtime loading;
- ORT API negotiation;
- ORT status;
- provider loading/configuration;
- platform packaging;
- allocation/overflow;
- unsupported feature.

## 4.4 Runtime loader

`dort_runtime_open` accepts a versioned configuration:

```c
typedef struct dort_runtime_config {
  uint32_t struct_size;
  uint32_t shim_abi_version;
  uint32_t required_ort_api_version;
  uint32_t source_kind;
  uint32_t flags;
  const char* library_path_utf8;
  const char* const* preferred_library_names_utf8;
  size_t preferred_library_name_count;
} dort_runtime_config_t;
```

The exact header may adjust names, but preserve these semantics.

### Source behavior

- **Linked:** resolve the linked `OrtGetApiBase` symbol; used for statically/framework-linked Apple builds and aligned builds where appropriate.
- **Bundled dynamic:** open the application-local ORT library selected by the build manifest.
- **Process:** try an already loaded symbol/handle, then a constrained list of platform library names. On Android this list normally contains only `libonnxruntime.so`.
- **Explicit file:** desktop only; require an absolute, policy-approved path.

### Platform loader requirements

#### POSIX/Linux/macOS

- Prefer absolute paths for bundled/explicit mode.
- Use `RTLD_NOW` so dependency failures occur at startup.
- Use `RTLD_LOCAL` unless a provider/platform requirement proves global visibility is needed.
- In process mode, consider `RTLD_NOLOAD` first where supported, then constrained `dlopen`.
- Copy `dlerror()` immediately.
- Do not unload by default.

#### Android

- The process-mode shim must not have `DT_NEEDED` for `libonnxruntime.so`.
- Resolve `OrtGetApiBase` with `dlsym` after opening the app-packaged library by SONAME.
- Keep the handle for process lifetime.
- Do not inspect or load private vendor libraries except through an EP's documented SDK/package contract.
- Test behavior with uncompressed and compressed native-library packaging modes and both load orders.

#### Windows

- Convert trusted UTF-8 paths to UTF-16 with error checking.
- Use an absolute path and `LoadLibraryExW` with safe search flags for bundled/explicit mode.
- Keep ORT and matching provider DLLs in the same application-local directory.
- Do not modify process-wide `PATH` as the normal loading strategy.
- Capture `GetLastError()` and format it into the project status.
- Do not call `FreeLibrary` by default.

#### iOS

- Do not depend on arbitrary runtime library loading.
- Link the shim and ORT through the selected framework/static packaging route.
- Use the linked-source mode and verify App Store-compatible embedding/signing.

## 4.5 ORT negotiation

The loader must:

```c
const OrtApiBase* base = OrtGetApiBase();
const char* version = base->GetVersionString();
const OrtApi* api = base->GetApi(required_api_version);
```

Check all pointers. If `GetApi` returns null, report:

- requested C API version;
- runtime version string;
- runtime source/identity;
- remediation (select compatible artifact or aligned build).

The shim never calls a function introduced after the negotiated API. A future API extension should use an explicit capability/ABI addition, not an unchecked cast.

## 4.6 Handle design

Opaque wrapper handles should contain:

- magic/type tag for debug validation;
- shim ABI version;
- atomic reference count where shared views require it;
- disposed/closing state;
- owning runtime identity pointer;
- underlying ORT handle;
- required auxiliary ownership (allocator, buffer, names, etc.).

Release functions accept null. A non-null retained reference must be released exactly once; a second call through the same freed pointer is not a supported native operation. The Dart layer provides idempotent `dispose()` by atomically clearing/detaching its native pointer before calling release. Debug builds should use type tags and optional quarantine/poisoning to catch misuse, but correctness must not depend on reading freed memory.

Do not expose the raw `Ort*` pointer to Dart.

Validation of a published value graph is read-only. When ONNX Runtime returns
a new value, the shim validates the complete graph and stores its computed
nesting depth only while that wrapper is still exclusively owned and
unpublished. Concurrent runs may therefore share one published input value
across sessions without a validation-time write race.

## 4.7 Environment and session functions

The shim should expose focused functions rather than one function per ORT setter. A session configuration can carry normalized fields and provider arrays. However, avoid one monolithic structure that cannot evolve.

Recommended pattern:

1. `dort_session_options_create`.
2. Typed setters for common stable options.
3. `dort_session_options_add_provider(provider_id, keys, values)`.
4. `dort_session_options_add_config_entry` escape hatch.
5. `dort_session_create_from_file` / `..._from_bytes`.
6. Release options after session creation.

Alternative immutable config structs are acceptable if every struct is size-versioned and optional fields have clear defaults.

Paths need platform adaptation. Windows model paths should use a shim function that converts UTF-8 to the native ORT character type rather than exposing wide strings to Dart.

## 4.8 Provider registration

Prefer the generic ONNX Runtime provider registration API available at the selected compatibility floor, behind a project-owned mapping:

```text
stable wrapper provider ID -> ORT registration mechanism/name
                           + string keys + string values
```

Do not conflate three namespaces: the wrapper ID (`xnnpack`), the generic C registration name (`XNNPACK`), and the provider name returned in availability/logging surfaces (commonly `XNNPACKExecutionProvider`). The mapping is versioned and tested against the pinned ORT build. An EP that requires a provider-specific factory or plugin API remains an implementation detail behind the wrapper ID.

Benefits:

- one stable shim entry point;
- newer provider options do not require immediate ABI changes;
- deprecated provider-specific flags remain outside Dart ABI;
- configuration can be logged and normalized.

The shim must distinguish:

- provider not compiled/known;
- provider shared library/dependency load failure;
- invalid provider option;
- provider registration success;
- later session/model incompatibility.

Never swallow a provider append error and proceed to CPU under strict policy.

The implemented API-27 adapter leaves CPU implicit and uses generic
registration only where API 27 documents it, including XNNPACK, CoreML, and
OpenVINO. CUDA and TensorRT use their V2 option-object APIs; oneDNN uses its
option-object API; MIGraphX uses its public typed options struct; and DirectML
is resolved through `GetExecutionProviderApi("DML", 27, ...)` on Windows. Each
provider-specific object is released on create, update, and append failure as
well as success. Pointer-bearing allocator, stream, cache-path, and
thread-pool options are outside the closed ABI-v1 maps.

The legacy Android-only NNAPI adapter resolves
`OrtSessionOptionsAppendExecutionProvider_Nnapi` from the exact opened runtime
module. NNAPI accepts only the closed 0--15 flag mask and fails as unsupported
on non-Android hosts or runtimes without that symbol. Runtime discovery is
copied through `dort_runtime_available_providers_json`; it is discovery
evidence only, not registration or assignment evidence. A compiled adapter is
likewise not evidence that the selected runtime contains the provider or that
any model node ran on it.

## 4.9 Tensor creation

For fixed-width tensors:

```c
dort_status_t* dort_tensor_create_with_buffer(
    dort_runtime_t* runtime,
    dort_buffer_t* buffer,
    size_t byte_offset,
    size_t byte_length,
    const int64_t* shape,
    size_t rank,
    int32_t element_type,
    dort_value_t** out_value);
```

Validation:

- rank pointer/count consistency;
- no negative concrete dimensions;
- checked element-count product;
- checked byte-count product;
- byte offset and byte length stay within the buffer, using checked addition;
- storage type and byte length match, including packed sub-byte element counts;
- requested alignment is a supported power of two and the resulting address is valid;
- buffer belongs to the same runtime/host memory domain where required.

Copy constructors can allocate `dort_buffer_t`, copy bytes, then reuse the same path.

String tensors use separate functions because their representation and allocation differ.

## 4.10 Output access

Provide metadata accessors and copy functions. Every pointer used to create a Dart native-backed view—including a direct wrapper buffer view—must be paired with a retained data lease:

```c
dort_status_t* dort_tensor_data_acquire(
    dort_value_t* tensor,
    dort_data_lease_t** out_lease,
    void** out_data,
    size_t* out_byte_length);

dort_status_t* dort_buffer_data_acquire(
    dort_buffer_t* buffer,
    dort_data_lease_t** out_lease,
    void** out_data,
    size_t* out_byte_length);

void dort_data_lease_release(dort_data_lease_t* lease);
```

The lease retains all native state necessary for the pointer to remain valid. Dart attaches the release function to the object/view that owns the lease. This is safer than returning an untracked pointer.

Device-resident tensors may not expose a host pointer. Return a precise unsupported/memory-domain error or provide an explicit copy-to-host API.

## 4.11 Runs

The baseline run function accepts arrays of names/values and requested output names. The shim validates array lengths and runtime identities, then delegates to ORT.

Consider returning a wrapper result that owns output name/value arrays so partial allocation failures are centrally released.

Cancellation uses the run-options handle through a bounded process-local
registry:

```c
dort_status_t* dort_cancel_token_register(
    dort_run_options_t* run_options,
    uint64_t* out_token);

dort_status_t* dort_cancel_token_request(
    uint64_t token,
    uint32_t* out_did_request);

dort_status_t* dort_cancel_token_finish(
    uint64_t token,
    uint32_t* out_was_requested);
```

The token is an opaque integer in `[1, INT64_MAX]`, suitable for a Dart isolate
message but not persistence or reuse. Registration retains run options. A
request sets termination at most once; unknown, stale, and already-finished
tokens fail closed. `finish` is called only after `Run` returns. If termination
was requested, it unsets termination before removing the entry and releasing
the registry retain. If unset fails, the entry and retain deliberately remain
live rather than freeing state that cannot be proven safe. The registry is
bounded to 1024 entries and reports a distinct full-registry error. Tokens are
monotonic and never wrap or alias a stale token; exhausting `[1, INT64_MAX]`
permanently closes registration for that process and reports the same bounded
exhaustion error.

The registry lock protects only membership and the closed
`registered`/`requesting`/`requested`/`unsetting` state machine. Native
`RunOptionsSetTerminate` and `RunOptionsUnsetTerminate` execute without that
process-wide lock. Same-token callers wait and re-resolve membership after the
in-flight native call; unrelated tokens continue independently. A failed set
restores the registered state for retry, while a failed unset restores the
requested state and preserves the authoritative registry retain.

Cross-thread cancellation follows ORT's documented thread-safety and maintains
the run-options lifetime until the run exits. A terminated run-options object
is not reused until the run has returned and the shim successfully calls the
corresponding unset/reset operation.

## 4.12 Logging and profiling

Avoid native callbacks into Dart for the initial implementation. Use:

- ORT default/platform logging;
- file-based profiling with caller-controlled directory;
- captured initialization diagnostics where a stable API exists;
- a native ring buffer polled by Dart only if necessary and carefully bounded.

Any ring buffer must avoid unbounded allocation and redact tensor data.

For strict per-run evidence, `dort_run_options_profiling_start` creates one
private child directory under an existing caller-owned artifact root and calls
ORT 1.25+ `RunOptionsEnableProfiling` with a shim-owned prefix.
On POSIX, the root is retained by descriptor and device/inode identity and must
be an existing absolute non-`/`, non-symlink directory owned by the effective
user, with owner read/write/search permission and no group/world write
permission. The child uses a CSPRNG-derived 128-bit leaf, mode `0700`, and its
own retained descriptor and device/inode identity.
`dort_run_options_profiling_finish` disables profiling, accepts exactly one
non-empty regular non-link profile up to 8 MiB, copies it into an owned
`dort_string_t`, accepts the pinned ORT basename shape
`profile_YYYY-MM-DD_HH-MM-SS_mmm.json`, rechecks the file identity and size
after its bounded read, and publishes bytes only after cleanup succeeds.

On POSIX, cleanup is bounded, non-recursive, and descriptor-relative. Identity
replacement, permission drift, unexpected nonempty children, incomplete
enumeration, or any checked cleanup failure takes precedence and publishes no
bytes. The retained POSIX owner blocks run-options reuse and permits an
explicit finish retry after the caller repairs its owned root. A replacement
observed before cleanup is left untouched while the retained original is
scrubbed through its descriptor. POSIX cannot remove a directory by
descriptor, so concurrent namespace mutation by other code running as the same
effective user is outside this isolation boundary. Windows retains its bounded
recursive cleanup contract and does not retain failed cleanup for retry.

If native disable itself fails, the shim likewise retains the private
directory and profiling state for retry. Final disposal releases the native
run-options owner before best-effort retirement (descriptor-relative on
POSIX), so a late native artifact cannot be recreated after cleanup. The
run-options retain used by synchronous Run/cancellation prevents cleanup before
Run returns.

## 4.13 Symbol visibility

On ELF platforms, the exact allowlist is
[`../src/fonix_exports.map`](../src/fonix_exports.map). Apple uses
[`../src/fonix_exports.apple`](../src/fonix_exports.apple), and Windows uses
[`../src/fonix_shim.def`](../src/fonix_shim.def) with controlled exports.

Audit with:

- `llvm-nm -D --defined-only` or platform equivalent;
- `llvm-readelf -Ws`;
- `dumpbin /exports` on Windows;
- `nm -gU` on Apple.

The exported set should contain only documented `dort_*` ABI entries and required initialization symbols.

## 4.14 Build hardening

Where supported:

- warnings as errors;
- stack protector;
- fortify/source hardening;
- RELRO/now on ELF application builds;
- non-executable stack;
- hidden visibility;
- control-flow/security flags appropriate to the platform toolchain;
- separate Address/UndefinedBehavior and ThreadSanitizer test builds;
- checked integer arithmetic helpers;
- no undefined signed overflow.

Do not enable a flag blindly when it breaks selected EP toolchains; record per-flavor exceptions in the lock/build manifest.

## 4.15 ABI tests

Required native tests:

- header compiles as C and C++ (with `extern "C"` behavior) but exposes C types only;
- struct sizes/offsets match generated Dart expectations on 32/64-bit targets supported;
- ABI version mismatch fails cleanly;
- unsupported ORT C API fails before environment creation;
- status survives runtime/session release;
- null/out-parameter behavior is deterministic;
- buffer/value lease reference counts cover all release orders;
- loader errors preserve platform error details;
- exported-symbol audit matches an allowlist;
- process mode has no link-time ORT dependency.

The canonical structural review record is
[`../release/native-c-abi-v1.json`](../release/native-c-abi-v1.json). It binds
the complete tokenized header and preprocessor surface, ABI constants, opaque
types, enum values, ordered struct fields, function prototypes, C-linkage and
platform calling/export directives, symbol version, and the ELF, Apple,
Windows, and Python export inventories. The native CTest contract also binds
the export and dependency auditors to isolated Python mode so a repository
file cannot shadow their standard-library imports. Verify it with:

```bash
python3 -B tool/ci/verify_native_c_abi_baseline.py --repository .
```

Header comments and declaration formatting alone do not change the structural
record. Any other header or export drift fails until the ABI impact is
reviewed and the canonical record is intentionally regenerated. A match does
not prove a target binary's layout or compatibility and does not replace
target ABI tests or external API/ABI approval.
