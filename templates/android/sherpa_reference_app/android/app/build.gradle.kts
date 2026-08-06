plugins {
    id("com.android.application")
    id("dev.flutter.flutter-gradle-plugin")
}

val fonixTargetPlatform = providers.gradleProperty("target-platform").orNull
check(fonixTargetPlatform == "android-arm64") {
    "The Fonix sherpa reference harness is pinned to android-arm64."
}

android {
    namespace = "dev.fonix.sherpa_reference"
    compileSdk = 36
    ndkVersion = "28.2.13676358"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "dev.fonix.sherpa_reference"
        minSdk = 24
        targetSdk = 36
        ndk {
            abiFilters.clear()
            abiFilters.add("arm64-v8a")
        }
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            isDebuggable = false
            isJniDebuggable = false
            isMinifyEnabled = true
            isShrinkResources = true
            // Local target qualification only; never distribution signing.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    packaging {
        jniLibs {
            useLegacyPackaging = false
            // Preserve the exact provenance-bound input ELFs. Android's default
            // Release stripping rewrites bytes in the first load segment of the
            // selected ORT publication, defeating source-to-final binding.
            keepDebugSymbols += setOf(
                "**/libonnxruntime.so",
                "**/libsherpa-onnx-c-api.so",
                "**/libsherpa-onnx-cxx-api.so",
                "**/libfonix_shim.so",
            )
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
