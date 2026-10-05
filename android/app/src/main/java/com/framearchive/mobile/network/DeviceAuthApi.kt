package com.framearchive.mobile.network

import com.framearchive.mobile.crypto.PublicJwk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.CookieJar
import okhttp3.Headers
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener
import java.io.IOException
import java.net.URI
import java.util.Base64
import java.util.Locale
import java.util.concurrent.TimeUnit

data class PairClaim(
    val pairingId: Long,
    val deviceId: Long,
    val claimSecret: String,
    val verificationCode: String,
)

sealed interface PairingStatus {
    data object Pending : PairingStatus
    data object Approved : PairingStatus
    data object Rejected : PairingStatus
    data object Expired : PairingStatus
}

data class AuthSession(val setCookieHeader: String)

open class DeviceAuthApiException(message: String, cause: Throwable? = null) : Exception(message, cause)
class PairingExpired(message: String = "Pairing request expired") : DeviceAuthApiException(message)
class PairingRejected(message: String = "Pairing request rejected") : DeviceAuthApiException(message)
class DeviceRevoked(message: String = "Device authentication was rejected") : DeviceAuthApiException(message)
class NetworkUnavailable(cause: IOException) : DeviceAuthApiException("Network unavailable", cause)

internal interface DeviceAuthService {
    suspend fun requestPairing(token: String, name: String, jwk: PublicJwk): PairClaim
    suspend fun pairingStatus(pairingId: Long, claimSecret: String): PairingStatus
    suspend fun challenge(deviceId: Long): String
    suspend fun verify(deviceId: Long, challenge: String, signature: ByteArray): AuthSession
    suspend fun isAuthenticated(): Boolean
}

class DeviceAuthApi(serverOrigin: String, client: OkHttpClient) : DeviceAuthService {
    private val origin: HttpUrl = strictOrigin(serverOrigin)
    private val client = client.newBuilder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .cookieJar(CookieJar.NO_COOKIES)
        .build()

    override suspend fun requestPairing(token: String, name: String, jwk: PublicJwk): PairClaim {
        require(token.isNotBlank()) { "Pairing token must not be blank" }
        require(name.isNotBlank()) { "Device name must not be blank" }
        val body = JSONObject()
            .put("token", token)
            .put("name", name)
            .put(
                "public_key",
                JSONObject()
                    .put("kty", jwk.kty)
                    .put("crv", jwk.crv)
                    .put("x", jwk.x)
                    .put("y", jwk.y),
            )
            .put("client_type", "android")
        val response = execute(post("/api/device-auth/pair", body))
        if (response.code == 409) throw PairingExpired(response.errorMessage(token))
        response.requireCode(202, token)
        val json = response.strictJsonObject()
        return PairClaim(
            pairingId = json.strictPositiveLong("pairing_id"),
            deviceId = json.strictPositiveLong("device_id"),
            claimSecret = json.strictNonBlankString("claim_secret"),
            verificationCode = json.strictNonBlankString("verification_code"),
        )
    }

    override suspend fun pairingStatus(pairingId: Long, claimSecret: String): PairingStatus {
        require(pairingId > 0) { "Pairing id must be positive" }
        require(claimSecret.isNotBlank()) { "Claim secret must not be blank" }
        val response = execute(
            post(
                "/api/device-auth/pair-status",
                JSONObject().put("pairing_id", pairingId).put("claim_secret", claimSecret),
            ),
        )
        if (response.code == 404) throw PairingExpired(response.errorMessage(claimSecret))
        if (response.code == 409) throw PairingRejected(response.errorMessage(claimSecret))
        response.requireCode(200, claimSecret)
        return when (response.strictJsonObject().strictNonBlankString("status").lowercase(Locale.ROOT)) {
            "pending", "open", "claiming" -> PairingStatus.Pending
            "approved" -> PairingStatus.Approved
            "rejected" -> PairingStatus.Rejected
            "expired" -> PairingStatus.Expired
            else -> throw DeviceAuthApiException("Pairing response contains an unknown status")
        }
    }

    override suspend fun challenge(deviceId: Long): String {
        require(deviceId > 0) { "Device id must be positive" }
        val response = execute(post("/api/device-auth/challenge", JSONObject().put("device_id", deviceId)))
        response.requireCode(200)
        return response.strictJsonObject().strictNonBlankString("challenge").requireBase64Url()
    }

    override suspend fun verify(deviceId: Long, challenge: String, signature: ByteArray): AuthSession {
        require(deviceId > 0) { "Device id must be positive" }
        require(challenge.isNotBlank()) { "Challenge must not be blank" }
        require(signature.size == P1363_SIGNATURE_BYTES) { "Signature must be a 64-byte P1363 value" }
        val encodedSignature = Base64.getUrlEncoder().withoutPadding().encodeToString(signature)
        val response = execute(
            post(
                "/api/device-auth/verify",
                JSONObject()
                    .put("device_id", deviceId)
                    .put("challenge", challenge)
                    .put("signature", encodedSignature),
            ),
        )
        if (response.code == 401) {
            throw DeviceRevoked(response.errorMessage(challenge, encodedSignature))
        }
        response.requireCode(200, challenge, encodedSignature)
        val matching = response.headers.values("Set-Cookie")
            .filter { header -> cookieName(header) == SESSION_COOKIE_NAME }
            .distinct()
        if (matching.size != 1) {
            throw DeviceAuthApiException("Authentication response contains an invalid session cookie")
        }
        return AuthSession(matching.single())
    }

    override suspend fun isAuthenticated(): Boolean {
        val response = execute(
            Request.Builder().url(url("/api/device-auth/status")).get().build(),
        )
        response.requireCode(200)
        return response.strictJsonObject().strictBoolean("authenticated")
    }

    private fun post(path: String, body: JSONObject): Request = Request.Builder()
        .url(url(path))
        .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
        .build()

    private fun url(path: String): HttpUrl = origin.resolve(path)
        ?: throw IllegalStateException("Unable to build device authentication URL")

    private suspend fun execute(request: Request): ApiResponse = withContext(Dispatchers.IO) {
        try {
            client.newCall(request).execute().use { response ->
                ApiResponse(response.code, response.headers, response.body?.string().orEmpty())
            }
        } catch (error: IOException) {
            throw NetworkUnavailable(error)
        }
    }

    private data class ApiResponse(val code: Int, val headers: Headers, val body: String) {
        fun requireCode(expected: Int, vararg secrets: String) {
            if (code != expected) throw DeviceAuthApiException(errorMessage(*secrets))
        }

        fun errorMessage(vararg secrets: String): String {
            val detail = try {
                strictJsonObject().opt("detail") as? String
            } catch (_: DeviceAuthApiException) {
                null
            }
            var safe = detail?.takeIf { it.isNotBlank() } ?: "Device authentication request failed"
            secrets.filter(String::isNotEmpty).forEach { secret -> safe = safe.replace(secret, "<redacted>") }
            return safe
        }

        fun strictJsonObject(): JSONObject {
            try {
                val tokener = JSONTokener(body)
                val value = tokener.nextValue() as? JSONObject
                    ?: throw DeviceAuthApiException("Device authentication response is invalid")
                if (tokener.nextClean() != 0.toChar()) {
                    throw DeviceAuthApiException("Device authentication response is invalid")
                }
                return value
            } catch (error: DeviceAuthApiException) {
                throw error
            } catch (error: JSONException) {
                throw DeviceAuthApiException("Device authentication response is invalid", error)
            }
        }
    }

    private companion object {
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        const val SESSION_COOKIE_NAME = "frame_device_session"
        const val P1363_SIGNATURE_BYTES = 64

        fun strictOrigin(raw: String): HttpUrl {
            val uri = try {
                URI(raw)
            } catch (error: Exception) {
                throw IllegalArgumentException("Server origin is invalid", error)
            }
            val scheme = uri.scheme?.lowercase(Locale.ROOT)
            require(scheme == "http" || scheme == "https") { "Server origin must use HTTP or HTTPS" }
            require(
                !uri.isOpaque && uri.rawAuthority != null && uri.rawUserInfo == null && uri.host != null &&
                    (uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") &&
                    uri.rawQuery == null && uri.rawFragment == null,
            ) { "Server address must be a strict origin" }
            val url = try {
                raw.toHttpUrl()
            } catch (error: IllegalArgumentException) {
                throw IllegalArgumentException("Server origin is invalid", error)
            }
            return url.newBuilder().encodedPath("/").query(null).fragment(null).build()
        }

        fun cookieName(header: String): String? {
            val pair = header.substringBefore(';').trim()
            val equals = pair.indexOf('=')
            if (equals <= 0) return null
            return pair.substring(0, equals).trim()
        }
    }
}

private fun JSONObject.strictPositiveLong(name: String): Long {
    val value = when (val raw = opt(name)) {
        is Int -> raw.toLong()
        is Long -> raw
        else -> throw DeviceAuthApiException("Device authentication response is invalid")
    }
    return value.takeIf { it > 0 }
        ?: throw DeviceAuthApiException("Device authentication response is invalid")
}

private fun JSONObject.strictNonBlankString(name: String): String =
    (opt(name) as? String)?.takeIf(String::isNotBlank)
        ?: throw DeviceAuthApiException("Device authentication response is invalid")

private fun JSONObject.strictBoolean(name: String): Boolean =
    opt(name) as? Boolean ?: throw DeviceAuthApiException("Device authentication response is invalid")

private fun String.requireBase64Url(): String {
    if (isEmpty() || any { it !in BASE64URL_CHARACTERS }) {
        throw DeviceAuthApiException("Device authentication response is invalid")
    }
    val decoded = try {
        Base64.getUrlDecoder().decode(this)
    } catch (error: IllegalArgumentException) {
        throw DeviceAuthApiException("Device authentication response is invalid", error)
    }
    if (decoded.isEmpty() || Base64.getUrlEncoder().withoutPadding().encodeToString(decoded) != this) {
        throw DeviceAuthApiException("Device authentication response is invalid")
    }
    return this
}

private val BASE64URL_CHARACTERS = ('A'..'Z') + ('a'..'z') + ('0'..'9') + setOf('-', '_')
