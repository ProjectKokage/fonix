// Illustrative application policy for Android coexistence.
// Adapt plugin versions, namespace, SDK levels, and task paths to the app.
//
// The consuming pubspec.yaml must select exactly one Fonix owner:
//
// hooks:
//   user_defines:
//     fonix:
//       android_runtime_owner: sherpa      # shim-only process mode
//
// or:
//
//       android_runtime_owner: application # exact lock-selected bundle
//       artifact_cache: /absolute/offline/cache
//
// An explicit runtime_mode, when present, must be external for sherpa and
// bundled for application ownership. Unknown or contradictory fields fail.

android {
    defaultConfig {
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    packaging {
        jniLibs {
            // PROHIBITED:
            // pickFirsts += "**/libonnxruntime.so"
            //
            // Select a single runtime owner instead:
            // - sherpa-owned mode: depend on the wrapper's external flavor;
            // - aligned mode: package the application-built ORT once.
        }
    }
}

// Run a final-artifact/native inventory task in CI. Dependency declarations
// are not sufficient because AGP merges, strips, and splits native libraries.
// A standalone Fonix invocation after building a final APK could be:
//
// tasks.register<Exec>("verifyReleaseOnnxRuntimeOwnership") {
//     dependsOn("assembleRelease")
//     commandLine(
//         "python3",
//         rootProject.file("tool/verify_native_libs.py"),
//         "--artifact",
//         layout.buildDirectory.file("outputs/apk/release/app-release.apk").get().asFile,
//         "--policy", "fonix-standalone-final",
//         "--require-16k-page-alignment",
//         "--require-abi", "arm64-v8a",
//         "--require-abi", "x86_64",
//     )
// }
//
// For a sherpa-owned final package, use "--policy", "sherpa-audit" together
// with --require-final-single-ort, --require-16k-page-alignment, the explicit
// ABI list, --reject-multiple-ort-owners, and
// --reject-multiple-libcxx-owners across the input audits. Generic mode is
// inventory only and does not invent dependency expectations.
