package dev.fonix.fonix_reference

import android.os.Handler
import android.os.Looper
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.nio.charset.StandardCharsets

class MainActivity : FlutterActivity() {
    private var smokeChannel: MethodChannel? = null
    private var completionAccepted = false

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val channel =
            MethodChannel(
                flutterEngine.dartExecutor.binaryMessenger,
                SMOKE_CHANNEL,
            )
        smokeChannel = channel
        channel.setMethodCallHandler { call, result ->
            if (call.method != COMPLETE_METHOD) {
                result.error(
                    "unsupported_smoke_method",
                    "The Android smoke channel received an unsupported method.",
                    null,
                )
                return@setMethodCallHandler
            }
            if (completionAccepted) {
                result.error(
                    "smoke_already_completed",
                    "The Android smoke completion was already accepted.",
                    null,
                )
                return@setMethodCallHandler
            }

            val completion = validateCompletion(call.arguments)
            if (completion == null) {
                result.error(
                    "invalid_smoke_completion",
                    "The Android smoke completion payload is invalid.",
                    null,
                )
                return@setMethodCallHandler
            }

            completionAccepted = true
            Log.i(SMOKE_LOG_TAG, completion.logLine)
            result.success(null)
            Handler(Looper.getMainLooper()).post {
                if (!isFinishing && !isDestroyed) {
                    finishAndRemoveTask()
                }
            }
        }
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        smokeChannel?.setMethodCallHandler(null)
        smokeChannel = null
        super.cleanUpFlutterEngine(flutterEngine)
    }

    private fun validateCompletion(arguments: Any?): SmokeCompletion? {
        val argumentsMap = arguments as? Map<*, *> ?: return null
        if (!hasExactStringKeys(argumentsMap, OUTER_KEYS)) return null
        if (argumentsMap["schemaVersion"] !is Int ||
            argumentsMap["schemaVersion"] != SCHEMA_VERSION
        ) {
            return null
        }
        val status = argumentsMap["status"] as? Int ?: return null
        if (status != STATUS_PASSED && status != STATUS_FAILED) return null
        val receipt = argumentsMap["receipt"] as? String ?: return null
        if (!isBoundedPrintableAscii(receipt, MAX_RECEIPT_BYTES)) return null

        val validReceipt =
            when (status) {
                STATUS_PASSED -> receipt == EXPECTED_PASSED_RECEIPT
                STATUS_FAILED -> FAILED_RECEIPT_PATTERN.matches(receipt)
                else -> false
            }
        if (!validReceipt) return null

        val prefix =
            if (status == STATUS_PASSED) {
                PASSED_PREFIX
            } else {
                FAILED_PREFIX
            }
        return SmokeCompletion(logLine = "$prefix$receipt")
    }

    private fun hasExactStringKeys(
        value: Map<*, *>,
        expected: Set<String>,
    ): Boolean {
        if (value.size != expected.size) return false
        val keys = value.keys
        return keys.all { it is String } && keys == expected
    }

    private fun isBoundedPrintableAscii(
        value: String,
        maximumBytes: Int,
    ): Boolean {
        if (value.isEmpty() ||
            value.toByteArray(StandardCharsets.UTF_8).size > maximumBytes
        ) {
            return false
        }
        return value.all { character -> character.code in PRINTABLE_ASCII_RANGE }
    }

    private data class SmokeCompletion(val logLine: String)

    private companion object {
        const val SMOKE_CHANNEL = "dev.fonix.reference/smoke"
        const val COMPLETE_METHOD = "complete"
        const val SMOKE_LOG_TAG = "FonixReference"
        const val PASSED_PREFIX = "FONIX_REFERENCE_RECEIPT="
        const val FAILED_PREFIX = "FONIX_REFERENCE_FAILURE="
        const val SCHEMA_VERSION = 1
        const val STATUS_PASSED = 0
        const val STATUS_FAILED = 1
        const val MAX_RECEIPT_BYTES = 16 * 1024
        const val EXPECTED_SHIM_BUILD_ID =
            "android-owner-application-source-bundled-artifact-" +
                "onnxruntime-1.27.1-android-arm64-v8a-cpu"
        const val EXPECTED_ARTIFACT_SHA256 =
            "9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383"
        // Dart's insertion-ordered map and jsonEncode emit this exact ASCII
        // sequence. Reordering, whitespace, or alternate JSON syntax fails.
        const val EXPECTED_PASSED_RECEIPT =
            "{\"schemaVersion\":1,\"status\":\"passed\"," +
                "\"runtimeVersion\":\"1.27.1\",\"runtimeSource\":\"bundled\"," +
                "\"runtimeOwner\":\"application\",\"artifactFlavor\":\"cpu\"," +
                "\"platform\":\"android\",\"architecture\":\"arm64-v8a\"," +
                "\"shimBuildId\":\"$EXPECTED_SHIM_BUILD_ID\"," +
                "\"artifactSha256\":\"$EXPECTED_ARTIFACT_SHA256\"," +
                "\"modelSha256\":" +
                "\"71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10\"," +
                "\"outputValues\":[1,4,9,16,25,36]," +
                "\"activeProviders\":[\"cpu\"],\"fullCpuAssignment\":true," +
                "\"doubleClose\":\"passed\"}"
        val PRINTABLE_ASCII_RANGE = 0x20..0x7e
        val FAILED_RECEIPT_PATTERN =
            Regex(
                """\A\{"schemaVersion":1,"status":"failed","errorType":"[A-Za-z][A-Za-z0-9_.]{0,63}"\}\z""",
            )
        val OUTER_KEYS = setOf("schemaVersion", "status", "receipt")
    }
}
