package dev.fonix.sherpa_reference

import android.util.Base64
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

class MainActivity : FlutterActivity() {
    private var harnessChannel: MethodChannel? = null
    private var validatedLaunch: ValidatedLaunch? = null
    private var completionAccepted = false

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val channel =
            MethodChannel(
                flutterEngine.dartExecutor.binaryMessenger,
                HARNESS_CHANNEL,
            )
        harnessChannel = channel
        channel.setMethodCallHandler { call, result ->
            when (call.method) {
                READ_LAUNCH_METHOD -> {
                    val launch = validatedLaunch ?: validateLaunch()
                    if (launch == null) {
                        result.error(
                            "invalid_harness_launch",
                            "The harness launch extras are missing or invalid.",
                            null,
                        )
                    } else {
                        validatedLaunch = launch
                        result.success(
                            mapOf(
                                "schemaVersion" to SCHEMA_VERSION,
                                "loadOrder" to launch.loadOrder,
                                "launchChallengeBase64" to launch.challengeBase64,
                            ),
                        )
                    }
                }
                COMPLETE_METHOD -> {
                    val launch = validatedLaunch
                    if (launch == null) {
                        result.error(
                            "launch_not_read",
                            "The harness launch must be read before completion.",
                            null,
                        )
                        return@setMethodCallHandler
                    }
                    if (completionAccepted) {
                        result.error(
                            "harness_already_completed",
                            "The harness completion was already accepted.",
                            null,
                        )
                        return@setMethodCallHandler
                    }
                    val completion = validateCompletion(call.arguments, launch)
                    if (completion == null) {
                        result.error(
                            "invalid_harness_completion",
                            "The harness completion envelope is invalid.",
                            null,
                        )
                        return@setMethodCallHandler
                    }

                    completionAccepted = true
                    logCompletion(completion)
                    result.success(null)
                    // The trusted host observes this same process after the
                    // receipt, then force-stops it. The app does not self-exit.
                }
                else -> {
                    result.error(
                        "unsupported_harness_method",
                        "The harness channel received an unsupported method.",
                        null,
                    )
                }
            }
        }
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        harnessChannel?.setMethodCallHandler(null)
        harnessChannel = null
        super.cleanUpFlutterEngine(flutterEngine)
    }

    private fun validateLaunch(): ValidatedLaunch? {
        val extras = intent.extras ?: return null
        if (extras.keySet() != LAUNCH_EXTRA_KEYS) return null
        val loadOrder = intent.getStringExtra(LOAD_ORDER_EXTRA) ?: return null
        if (loadOrder != DART_FIRST && loadOrder != SHERPA_FIRST) return null
        val challengeBase64 =
            intent.getStringExtra(LAUNCH_CHALLENGE_EXTRA) ?: return null
        if (!isBoundedPrintableAscii(challengeBase64, MAX_CHALLENGE_BASE64_BYTES)) {
            return null
        }
        val challenge =
            try {
                Base64.decode(challengeBase64, Base64.NO_WRAP)
            } catch (_: IllegalArgumentException) {
                return null
            }
        if (challenge.isEmpty() ||
            challenge.size > MAX_CHALLENGE_BYTES ||
            Base64.encodeToString(challenge, Base64.NO_WRAP) != challengeBase64
        ) {
            return null
        }
        return ValidatedLaunch(
            loadOrder = loadOrder,
            challengeBase64 = challengeBase64,
            challengeSha256 = sha256(challenge),
        )
    }

    private fun validateCompletion(
        arguments: Any?,
        launch: ValidatedLaunch,
    ): HarnessCompletion? {
        val map = arguments as? Map<*, *> ?: return null
        if (!hasExactStringKeys(map, COMPLETION_KEYS)) return null
        if (map["schemaVersion"] != SCHEMA_VERSION) return null
        val status = map["status"] as? String ?: return null
        if (status !in COMPLETION_STATUSES) return null
        val payloadSha256 = map["payloadSha256"] as? String ?: return null
        if (!SHA256_PATTERN.matches(payloadSha256)) return null
        val payloadJson = map["payloadJson"] as? String ?: return null
        if (!isBoundedPrintableAscii(payloadJson, MAX_COMPLETION_BYTES)) {
            return null
        }
        val payloadBytes = payloadJson.toByteArray(StandardCharsets.UTF_8)
        if (sha256(payloadBytes) != payloadSha256) return null

        val payload =
            try {
                JSONObject(payloadJson)
            } catch (_: Exception) {
                return null
            }
        val expectedKeys =
            if (status == PASSED) {
                PASSED_PAYLOAD_KEYS
            } else {
                UNAVAILABLE_PAYLOAD_KEYS
            }
        val payloadSchema = payload.opt("schemaVersion")
        val payloadResult = payload.opt("result")
        val payloadLoadOrder = payload.opt("loadOrder")
        val payloadChallengeSha256 = payload.opt("launchChallengeSha256")
        if (!hasExactJsonKeys(payload, expectedKeys) ||
            payloadSchema !is Int ||
            payloadSchema != SCHEMA_VERSION ||
            payloadResult !is String ||
            payloadResult != status ||
            payloadLoadOrder !is String ||
            payloadLoadOrder != launch.loadOrder ||
            payloadChallengeSha256 !is String ||
            payloadChallengeSha256 != launch.challengeSha256
        ) {
            return null
        }
        if (status == PASSED) {
            for (key in PASSED_OBJECT_KEYS) {
                if (payload.opt(key) !is JSONObject) return null
            }
        } else {
            val reason = payload.opt("reason")
            if (reason !is String) return null
            if (status == UNAVAILABLE && reason != NATIVE_FIXTURES_UNPROVISIONED) {
                return null
            }
            if (status == FAILED && reason != QUALIFICATION_FAILED) return null
        }
        return HarnessCompletion(status, payloadSha256, payloadJson)
    }

    private fun logCompletion(completion: HarnessCompletion) {
        val chunks = completion.payloadJson.chunked(LOG_CHUNK_CHARACTERS)
        Log.i(
            LOG_TAG,
            listOf(
                BEGIN_MARKER,
                SCHEMA_VERSION.toString(),
                completion.status,
                completion.payloadSha256,
                completion.payloadJson.length.toString(),
                chunks.size.toString(),
            ).joinToString("|"),
        )
        chunks.forEachIndexed { index, chunk ->
            Log.i(
                LOG_TAG,
                listOf(
                    CHUNK_MARKER,
                    completion.payloadSha256,
                    index.toString(),
                    chunk,
                ).joinToString("|"),
            )
        }
        Log.i(LOG_TAG, "$END_MARKER|${completion.payloadSha256}")
    }

    private fun hasExactStringKeys(
        value: Map<*, *>,
        expected: Set<String>,
    ): Boolean =
        value.size == expected.size &&
            value.keys.all { key -> key is String } &&
            value.keys == expected

    private fun hasExactJsonKeys(
        value: JSONObject,
        expected: Set<String>,
    ): Boolean {
        if (value.length() != expected.size) return false
        val names = value.keys().asSequence().toSet()
        return names == expected
    }

    private fun isBoundedPrintableAscii(
        value: String,
        maximumBytes: Int,
    ): Boolean {
        if (value.isEmpty()) return false
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return bytes.size <= maximumBytes &&
            value.all { character -> character.code in PRINTABLE_ASCII_RANGE }
    }

    private fun sha256(value: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value)
        val alphabet = "0123456789abcdef"
        return buildString(digest.size * 2) {
            for (byte in digest) {
                val unsigned = byte.toInt() and 0xff
                append(alphabet[unsigned ushr 4])
                append(alphabet[unsigned and 0x0f])
            }
        }
    }

    private data class ValidatedLaunch(
        val loadOrder: String,
        val challengeBase64: String,
        val challengeSha256: String,
    )

    private data class HarnessCompletion(
        val status: String,
        val payloadSha256: String,
        val payloadJson: String,
    )

    private companion object {
        const val HARNESS_CHANNEL = "dev.fonix.sherpa_reference/harness"
        const val READ_LAUNCH_METHOD = "readLaunch"
        const val COMPLETE_METHOD = "complete"
        const val LOAD_ORDER_EXTRA =
            "dev.fonix.sherpa_reference.LOAD_ORDER"
        const val LAUNCH_CHALLENGE_EXTRA =
            "dev.fonix.sherpa_reference.LAUNCH_CHALLENGE_BASE64"
        const val SCHEMA_VERSION = 1
        const val DART_FIRST = "dart-first"
        const val SHERPA_FIRST = "sherpa-first"
        const val PASSED = "passed"
        const val UNAVAILABLE = "unavailable"
        const val FAILED = "failed"
        const val NATIVE_FIXTURES_UNPROVISIONED =
            "native-fixtures-unprovisioned"
        const val QUALIFICATION_FAILED = "qualification-failed"
        const val MAX_CHALLENGE_BYTES = 4096
        const val MAX_CHALLENGE_BASE64_BYTES = 5464
        const val MAX_COMPLETION_BYTES = 512 * 1024
        const val LOG_CHUNK_CHARACTERS = 3000
        const val LOG_TAG = "FonixSherpaRef"
        const val BEGIN_MARKER = "FONIX_SHERPA_COMPLETION_BEGIN"
        const val CHUNK_MARKER = "FONIX_SHERPA_COMPLETION_CHUNK"
        const val END_MARKER = "FONIX_SHERPA_COMPLETION_END"
        val PRINTABLE_ASCII_RANGE = 0x20..0x7e
        val SHA256_PATTERN = Regex("^[0-9a-f]{64}$")
        val COMPLETION_STATUSES = setOf(PASSED, UNAVAILABLE, FAILED)
        val LAUNCH_EXTRA_KEYS = setOf(LOAD_ORDER_EXTRA, LAUNCH_CHALLENGE_EXTRA)
        val COMPLETION_KEYS =
            setOf("schemaVersion", "status", "payloadSha256", "payloadJson")
        val PASSED_PAYLOAD_KEYS =
            setOf(
                "schemaVersion",
                "result",
                "launchChallengeSha256",
                "loadOrder",
                "runtime",
                "sherpa",
                "fixtures",
                "initialization",
                "workload",
                "lifecycle",
            )
        val PASSED_OBJECT_KEYS =
            setOf(
                "runtime",
                "sherpa",
                "fixtures",
                "initialization",
                "workload",
                "lifecycle",
            )
        val UNAVAILABLE_PAYLOAD_KEYS =
            setOf(
                "schemaVersion",
                "result",
                "launchChallengeSha256",
                "loadOrder",
                "reason",
            )
    }
}
