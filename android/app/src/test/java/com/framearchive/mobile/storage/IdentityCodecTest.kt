package com.framearchive.mobile.storage

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.InvalidAlgorithmParameterException
import java.security.NoSuchAlgorithmException
import java.security.ProviderException
import java.security.UnrecoverableKeyException
import javax.crypto.AEADBadTagException
import javax.crypto.BadPaddingException

class IdentityCodecTest {
    @Test
    fun `identity without pending round trips exactly`() {
        val identity = DeviceIdentity(
            serverOrigin = "https://例子.test:8443",
            deviceId = Long.MAX_VALUE,
            deviceName = "客厅 📷",
            keyAlias = "设备密钥-α",
        )

        assertEquals(identity, IdentityCodec.decode(IdentityCodec.encode(identity)))
    }

    @Test
    fun `identity with pending round trips exactly`() {
        val identity = validIdentity(
            pending = PendingPairing(
                pairingId = Long.MAX_VALUE,
                claimSecret = "secret-绝密",
                verificationCode = "９８７６５４",
                expiresAtEpochMillis = Long.MAX_VALUE,
            ),
        )

        assertEquals(identity, IdentityCodec.decode(IdentityCodec.encode(identity)))
    }

    @Test
    fun `encoding identity without pending omits pending secrets`() {
        val withoutPending = validIdentity().copy(pending = null)

        val encoded = IdentityCodec.encode(withoutPending)

        assertFalse(encoded.contains("pending"))
        assertFalse(encoded.contains("claimSecret"))
        assertFalse(encoded.contains("verificationCode"))
        assertFalse(encoded.contains("very-secret"))
    }

    @Test
    fun `encoding uses versioned top level envelope`() {
        val envelope = JSONObject(IdentityCodec.encode(validIdentity(pending = null)))

        assertEquals(setOf("version", "identity"), envelope.keys().asSequence().toSet())
        assertEquals(1, envelope.get("version"))
        assertTrue(envelope.get("identity") is JSONObject)
    }

    @Test
    fun `unknown missing and wrongly typed versions are rejected`() {
        listOf(
            """{"version":2,"identity":${identityJson()}}""",
            """{"identity":${identityJson()}}""",
            """{"version":"1","identity":${identityJson()}}""",
            """{"version":1.0,"identity":${identityJson()}}""",
            """{"version":null,"identity":${identityJson()}}""",
        ).forEach(::assertRejected)
    }

    @Test
    fun `malformed and non-object top levels are rejected`() {
        listOf(
            "not-json",
            "[]",
            "null",
            "42",
            "\"text\"",
        ).forEach(::assertRejected)
    }

    @Test
    fun `missing identity or required identity fields are rejected`() {
        listOf(
            """{"version":1}""",
            """{"version":1,"identity":null}""",
            """{"version":1,"identity":[]}""",
            """{"version":1,"identity":{"deviceId":7,"deviceName":"phone","keyAlias":"key"}}""",
            """{"version":1,"identity":{"serverOrigin":"https://example.com","deviceName":"phone","keyAlias":"key"}}""",
            """{"version":1,"identity":{"serverOrigin":"https://example.com","deviceId":7,"keyAlias":"key"}}""",
            """{"version":1,"identity":{"serverOrigin":"https://example.com","deviceId":7,"deviceName":"phone"}}""",
        ).forEach(::assertRejected)
    }

    @Test
    fun `wrongly typed identity fields and overflowing integers are rejected`() {
        listOf(
            identityJson(serverOrigin = "7"),
            identityJson(deviceId = "\"7\""),
            identityJson(deviceId = "7.0"),
            identityJson(deviceId = "9223372036854775808"),
            identityJson(deviceName = "false"),
            identityJson(keyAlias = "{}"),
        ).map { """{"version":1,"identity":$it}""" }
            .forEach(::assertRejected)
    }

    @Test
    fun `blank strings non-positive ids and expiry are rejected`() {
        listOf(
            identityJson(serverOrigin = "\"\""),
            identityJson(deviceName = "\"   \""),
            identityJson(keyAlias = "\"\t\""),
            identityJson(deviceId = "0"),
            identityJson(deviceId = "-1"),
            identityJson(pending = pendingJson(pairingId = "0")),
            identityJson(pending = pendingJson(pairingId = "-1")),
            identityJson(pending = pendingJson(claimSecret = "\"\"")),
            identityJson(pending = pendingJson(verificationCode = "\" \"")),
            identityJson(pending = pendingJson(expiresAt = "0")),
            identityJson(pending = pendingJson(expiresAt = "-1")),
        ).map { """{"version":1,"identity":$it}""" }
            .forEach(::assertRejected)
    }

    @Test
    fun `partially present or invalid pending is rejected as a whole`() {
        listOf(
            "{}",
            """{"pairingId":9}""",
            """{"pairingId":9,"claimSecret":"secret","verificationCode":"123456"}""",
            pendingJson(pairingId = "\"9\""),
            pendingJson(claimSecret = "null"),
            pendingJson(verificationCode = "9"),
            pendingJson(expiresAt = "1.5"),
            pendingJson(expiresAt = "9223372036854775808"),
        ).map { """{"version":1,"identity":${identityJson(pending = it)}}""" }
            .forEach(::assertRejected)
    }

    @Test
    fun `codec errors do not reveal claim secret`() {
        val secret = "do-not-leak-this-secret"
        val malformedPending = pendingJson(claimSecret = JSONObject.quote(secret), expiresAt = "\"bad\"")

        val error = assertThrows(IdentityCodecException::class.java) {
            IdentityCodec.decode("""{"version":1,"identity":${identityJson(pending = malformedPending)}}""")
        }

        assertFalse(error.message.orEmpty().contains(secret))
    }

    @Test
    fun `unknown fields are ignored for same version`() {
        val json = """
            {
              "version": 1,
              "futureEnvelopeField": {"enabled": true},
              "identity": {
                "serverOrigin": "https://example.com",
                "deviceId": 7,
                "deviceName": "phone",
                "keyAlias": "key",
                "futureIdentityField": [1, 2, 3],
                "pending": {
                  "pairingId": 9,
                  "claimSecret": "secret",
                  "verificationCode": "123456",
                  "expiresAtEpochMillis": 99,
                  "futurePendingField": "ok"
                }
              }
            }
        """.trimIndent()

        assertEquals(validIdentity(), IdentityCodec.decode(json))
    }

    @Test
    fun `trailing content after identity envelope is rejected`() {
        listOf(
            """{"version":1,"identity":${identityJson()}} garbage""",
            """{"version":1,"identity":${identityJson()}} {}""",
            """{"version":1,"identity":${identityJson()}} []""",
        ).forEach(::assertRejected)
    }

    @Test
    fun `identity string representations redact pending secrets`() {
        val secret = "actual-claim-secret"
        val code = "actual-verification-code"
        val pending = PendingPairing(9, secret, code, 99)
        val identity = validIdentity(pending)

        listOf(pending.toString(), identity.toString()).forEach { representation ->
            assertFalse(representation.contains(secret))
            assertFalse(representation.contains(code))
        }
    }

    @Test
    fun `read security failure classification clears only known corruption`() {
        listOf(
            AEADBadTagException(),
            BadPaddingException(),
            UnrecoverableKeyException(),
        ).forEach { error ->
            assertEquals(IdentityReadFailure.CORRUPTED, classifyIdentityReadSecurityFailure(error))
        }

        listOf(
            NoSuchAlgorithmException(),
            InvalidAlgorithmParameterException(),
            ProviderException(),
        ).forEach { error ->
            assertEquals(IdentityReadFailure.UNAVAILABLE, classifyIdentityReadSecurityFailure(error))
        }
    }

    private fun assertRejected(json: String) {
        assertThrows("Expected rejection for $json", IdentityCodecException::class.java) {
            IdentityCodec.decode(json)
        }
    }

    private fun validIdentity(pending: PendingPairing? = PendingPairing(9, "secret", "123456", 99)) =
        DeviceIdentity(
            serverOrigin = "https://example.com",
            deviceId = 7,
            deviceName = "phone",
            keyAlias = "key",
            pending = pending,
        )

    private fun identityJson(
        serverOrigin: String = "\"https://example.com\"",
        deviceId: String = "7",
        deviceName: String = "\"phone\"",
        keyAlias: String = "\"key\"",
        pending: String? = null,
    ): String = buildString {
        append("""{"serverOrigin":$serverOrigin,"deviceId":$deviceId,"deviceName":$deviceName,"keyAlias":$keyAlias""")
        if (pending != null) append(",\"pending\":$pending")
        append("}")
    }

    private fun pendingJson(
        pairingId: String = "9",
        claimSecret: String = "\"secret\"",
        verificationCode: String = "\"123456\"",
        expiresAt: String = "99",
    ): String = """{"pairingId":$pairingId,"claimSecret":$claimSecret,"verificationCode":$verificationCode,"expiresAtEpochMillis":$expiresAt}"""
}
