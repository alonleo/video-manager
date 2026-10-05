package com.framearchive.mobile.storage

import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener

data class DeviceIdentity(
    val serverOrigin: String,
    val deviceId: Long,
    val deviceName: String,
    val keyAlias: String,
    val pending: PendingPairing? = null,
) {
    override fun toString(): String =
        "DeviceIdentity(serverOrigin=$serverOrigin, deviceId=$deviceId, " +
            "deviceName=$deviceName, keyAlias=$keyAlias, pending=${pending != null})"
}

data class PendingPairing(
    val pairingId: Long,
    val claimSecret: String,
    val verificationCode: String,
    val expiresAtEpochMillis: Long,
) {
    override fun toString(): String =
        "PendingPairing(pairingId=$pairingId, claimSecret=<redacted>, " +
            "verificationCode=<redacted>, expiresAtEpochMillis=$expiresAtEpochMillis)"
}

internal class IdentityCodecException(
    message: String,
    cause: Throwable? = null,
) : IllegalArgumentException(message, cause)

internal object IdentityCodec {
    fun encode(identity: DeviceIdentity): String {
        validate(identity)

        return try {
            val identityJson = JSONObject()
                .put(SERVER_ORIGIN, identity.serverOrigin)
                .put(DEVICE_ID, identity.deviceId)
                .put(DEVICE_NAME, identity.deviceName)
                .put(KEY_ALIAS, identity.keyAlias)

            identity.pending?.let { pending ->
                identityJson.put(
                    PENDING,
                    JSONObject()
                        .put(PAIRING_ID, pending.pairingId)
                        .put(CLAIM_SECRET, pending.claimSecret)
                        .put(VERIFICATION_CODE, pending.verificationCode)
                        .put(EXPIRES_AT, pending.expiresAtEpochMillis),
                )
            }

            JSONObject()
                .put(VERSION, CURRENT_VERSION)
                .put(IDENTITY, identityJson)
                .toString()
        } catch (error: JSONException) {
            throw IdentityCodecException(INVALID_IDENTITY, error)
        }
    }

    fun decode(encoded: String): DeviceIdentity {
        try {
            val input = JSONTokener(encoded)
            val envelope = input.nextValue() as? JSONObject
                ?: throw IdentityCodecException(INVALID_IDENTITY)
            if (input.nextClean() != 0.toChar()) {
                throw IdentityCodecException(INVALID_IDENTITY)
            }
            if (envelope.strictLong(VERSION) != CURRENT_VERSION.toLong()) {
                throw IdentityCodecException(UNSUPPORTED_VERSION)
            }

            val identityJson = envelope.strictObject(IDENTITY)
            val pending = if (identityJson.has(PENDING)) {
                val pendingJson = identityJson.strictObject(PENDING)
                PendingPairing(
                    pairingId = pendingJson.strictPositiveLong(PAIRING_ID),
                    claimSecret = pendingJson.strictNonBlankString(CLAIM_SECRET),
                    verificationCode = pendingJson.strictNonBlankString(VERIFICATION_CODE),
                    expiresAtEpochMillis = pendingJson.strictPositiveLong(EXPIRES_AT),
                )
            } else {
                null
            }

            return DeviceIdentity(
                serverOrigin = identityJson.strictNonBlankString(SERVER_ORIGIN),
                deviceId = identityJson.strictPositiveLong(DEVICE_ID),
                deviceName = identityJson.strictNonBlankString(DEVICE_NAME),
                keyAlias = identityJson.strictNonBlankString(KEY_ALIAS),
                pending = pending,
            )
        } catch (error: IdentityCodecException) {
            throw error
        } catch (error: JSONException) {
            throw IdentityCodecException(INVALID_IDENTITY, error)
        } catch (error: IllegalArgumentException) {
            throw IdentityCodecException(INVALID_IDENTITY, error)
        }
    }

    private fun validate(identity: DeviceIdentity) {
        if (
            identity.serverOrigin.isBlank() ||
            identity.deviceId <= 0 ||
            identity.deviceName.isBlank() ||
            identity.keyAlias.isBlank()
        ) {
            throw IdentityCodecException(INVALID_IDENTITY)
        }

        identity.pending?.let { pending ->
            if (
                pending.pairingId <= 0 ||
                pending.claimSecret.isBlank() ||
                pending.verificationCode.isBlank() ||
                pending.expiresAtEpochMillis <= 0
            ) {
                throw IdentityCodecException(INVALID_IDENTITY)
            }
        }
    }

    private fun JSONObject.strictObject(name: String): JSONObject =
        opt(name) as? JSONObject ?: throw IdentityCodecException(INVALID_IDENTITY)

    private fun JSONObject.strictNonBlankString(name: String): String {
        val value = opt(name) as? String ?: throw IdentityCodecException(INVALID_IDENTITY)
        return value.takeIf(String::isNotBlank) ?: throw IdentityCodecException(INVALID_IDENTITY)
    }

    private fun JSONObject.strictPositiveLong(name: String): Long =
        strictLong(name).takeIf { it > 0 } ?: throw IdentityCodecException(INVALID_IDENTITY)

    private fun JSONObject.strictLong(name: String): Long = when (val value = opt(name)) {
        is Int -> value.toLong()
        is Long -> value
        else -> throw IdentityCodecException(INVALID_IDENTITY)
    }

    private const val CURRENT_VERSION = 1
    private const val VERSION = "version"
    private const val IDENTITY = "identity"
    private const val SERVER_ORIGIN = "serverOrigin"
    private const val DEVICE_ID = "deviceId"
    private const val DEVICE_NAME = "deviceName"
    private const val KEY_ALIAS = "keyAlias"
    private const val PENDING = "pending"
    private const val PAIRING_ID = "pairingId"
    private const val CLAIM_SECRET = "claimSecret"
    private const val VERIFICATION_CODE = "verificationCode"
    private const val EXPIRES_AT = "expiresAtEpochMillis"
    private const val INVALID_IDENTITY = "Stored device identity is invalid"
    private const val UNSUPPORTED_VERSION = "Stored device identity version is unsupported"
}
