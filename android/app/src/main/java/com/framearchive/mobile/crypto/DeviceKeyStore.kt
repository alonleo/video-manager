package com.framearchive.mobile.crypto

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.util.Base64

data class PublicJwk(
    val kty: String = "EC",
    val crv: String = "P-256",
    val x: String,
    val y: String,
)

internal object PublicJwkEncoder {
    private const val COORDINATE_SIZE = 32

    fun encode(publicKey: ECPublicKey): PublicJwk = PublicJwk(
        x = encodeCoordinate(publicKey.w.affineX),
        y = encodeCoordinate(publicKey.w.affineY),
    )

    fun encodeCoordinate(coordinate: BigInteger): String {
        require(coordinate.signum() >= 0) { "EC coordinate must not be negative" }

        var unsigned = coordinate.toByteArray()
        if (unsigned.size == COORDINATE_SIZE + 1 && unsigned[0] == 0.toByte()) {
            unsigned = unsigned.copyOfRange(1, unsigned.size)
        }
        require(unsigned.size <= COORDINATE_SIZE) { "EC coordinate exceeds 32 bytes" }

        val fixedWidth = ByteArray(COORDINATE_SIZE)
        unsigned.copyInto(fixedWidth, destinationOffset = COORDINATE_SIZE - unsigned.size)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(fixedWidth)
    }
}

class DeviceKeyStore {
    /** Returns the existing public key for [alias] instead of replacing device identity. */
    fun create(alias: String): PublicJwk {
        requireAlias(alias)
        if (contains(alias)) {
            return publicJwk(alias)
        }

        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_EC,
            ANDROID_KEY_STORE,
        )
        val parameters = KeyGenParameterSpec.Builder(
            alias,
            KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
        )
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .build()
        generator.initialize(parameters)
        val publicKey = generator.generateKeyPair().public as? ECPublicKey
            ?: throw IllegalStateException("Generated key for alias '$alias' is not an EC public key")
        return PublicJwkEncoder.encode(publicKey)
    }

    fun publicJwk(alias: String): PublicJwk {
        requireAlias(alias)
        val certificate = keyStore().getCertificate(alias)
            ?: throw IllegalStateException("No Android Keystore key exists for alias '$alias'")
        val publicKey = certificate.publicKey as? ECPublicKey
            ?: throw IllegalStateException("Key for alias '$alias' is not an EC public key")
        return PublicJwkEncoder.encode(publicKey)
    }

    fun sign(alias: String, challenge: ByteArray): ByteArray {
        requireAlias(alias)
        val privateKey = keyStore().getKey(alias, null) as? PrivateKey
            ?: throw IllegalStateException("No Android Keystore private key exists for alias '$alias'")
        return Signature.getInstance("SHA256withECDSA").run {
            initSign(privateKey)
            update(challenge)
            DerSignatureConverter.toP1363(sign())
        }
    }

    fun delete(alias: String) {
        requireAlias(alias)
        val store = keyStore()
        if (store.containsAlias(alias)) {
            store.deleteEntry(alias)
        }
    }

    fun contains(alias: String): Boolean {
        requireAlias(alias)
        return keyStore().containsAlias(alias)
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply {
        load(null)
    }

    private fun requireAlias(alias: String) {
        require(alias.isNotBlank()) { "Key alias must not be blank" }
    }

    private companion object {
        const val ANDROID_KEY_STORE = "AndroidKeyStore"
    }
}
