package com.framearchive.mobile.storage

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

interface IdentityStore {
    fun read(): DeviceIdentity?

    fun save(identity: DeviceIdentity)

    fun clear()
}

class IdentityStoreCorrupted(
    message: String,
    cause: Throwable? = null,
) : IllegalStateException(message, cause)

internal object GcmIvValidator {
    fun requireValid(iv: ByteArray?): ByteArray =
        iv?.takeIf { it.size == GCM_IV_SIZE_BYTES }
            ?: throw IllegalStateException("Android Keystore did not generate a valid GCM IV")
}

private const val GCM_IV_SIZE_BYTES = 12

class EncryptedIdentityStore(context: Context) : IdentityStore {
    private val preferences: SharedPreferences =
        (context.applicationContext ?: context).getSharedPreferences(PREFERENCES_FILE, Context.MODE_PRIVATE)
    @Synchronized
    override fun read(): DeviceIdentity? {
        val hasIv = preferences.contains(IV_KEY)
        val hasCiphertext = preferences.contains(CIPHERTEXT_KEY)
        if (!hasIv && !hasCiphertext) return null
        if (!hasIv || !hasCiphertext) {
            clearCorruptedAndThrow("Stored device identity is incomplete")
        }

        val encodedIv: String
        val encodedCiphertext: String
        try {
            encodedIv = preferences.getString(IV_KEY, null)
                ?: clearCorruptedAndThrow("Stored device identity is incomplete")
            encodedCiphertext = preferences.getString(CIPHERTEXT_KEY, null)
                ?: clearCorruptedAndThrow("Stored device identity is incomplete")
        } catch (error: ClassCastException) {
            clearCorruptedAndThrow("Stored device identity has an invalid representation", error)
        }

        val iv: ByteArray
        val ciphertext: ByteArray
        try {
            iv = Base64.getDecoder().decode(encodedIv)
            ciphertext = Base64.getDecoder().decode(encodedCiphertext)
        } catch (error: IllegalArgumentException) {
            clearCorruptedAndThrow("Stored device identity is not valid base64", error)
        }
        if (iv.size != GCM_IV_SIZE_BYTES) {
            clearCorruptedAndThrow("Stored device identity has an invalid IV")
        }

        val plaintext = try {
            val key = existingKey()
                ?: clearCorruptedAndThrow("Stored device identity key is unavailable")
            Cipher.getInstance(CIPHER_TRANSFORMATION).run {
                init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_SIZE_BITS, iv))
                doFinal(ciphertext)
            }
        } catch (error: GeneralSecurityException) {
            clearCorruptedAndThrow("Stored device identity cannot be decrypted", error)
        } catch (error: IOException) {
            clearCorruptedAndThrow("Stored device identity key cannot be loaded", error)
        } catch (error: IllegalArgumentException) {
            clearCorruptedAndThrow("Stored device identity encryption parameters are invalid", error)
        }

        return try {
            IdentityCodec.decode(String(plaintext, StandardCharsets.UTF_8))
        } catch (error: IdentityCodecException) {
            clearCorruptedAndThrow("Stored device identity payload is invalid", error)
        }
    }

    @Synchronized
    override fun save(identity: DeviceIdentity) {
        val plaintext = IdentityCodec.encode(identity).toByteArray(StandardCharsets.UTF_8)
        val encrypted = try {
            Cipher.getInstance(CIPHER_TRANSFORMATION).run {
                init(Cipher.ENCRYPT_MODE, getOrCreateKey())
                val providerIv = GcmIvValidator.requireValid(iv)
                EncryptedPayload(providerIv, doFinal(plaintext))
            }
        } catch (error: GeneralSecurityException) {
            throw IllegalStateException("Unable to encrypt device identity", error)
        } catch (error: IOException) {
            throw IllegalStateException("Unable to load the device identity key", error)
        }

        val committed = preferences.edit()
            .clear()
            .putString(IV_KEY, Base64.getEncoder().encodeToString(encrypted.iv))
            .putString(CIPHERTEXT_KEY, Base64.getEncoder().encodeToString(encrypted.ciphertext))
            .commit()
        if (!committed) {
            throw IllegalStateException("Unable to persist encrypted device identity")
        }
    }

    @Synchronized
    override fun clear() {
        if (!preferences.edit().clear().commit()) {
            throw IllegalStateException("Unable to clear encrypted device identity")
        }
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun existingKey(): SecretKey? {
        val store = keyStore()
        if (!store.containsAlias(KEY_ALIAS)) return null
        return store.getKey(KEY_ALIAS, null) as? SecretKey
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun getOrCreateKey(): SecretKey {
        existingKey()?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE)
        val parameters = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setKeySize(KEY_SIZE_BITS)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setUserAuthenticationRequired(false)
            .build()
        generator.init(parameters)
        return generator.generateKey()
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply {
        load(null)
    }

    private fun clearCorruptedAndThrow(message: String, cause: Throwable? = null): Nothing {
        val cleared = preferences.edit().clear().commit()
        val error = IdentityStoreCorrupted(message, cause)
        if (!cleared) {
            error.addSuppressed(IllegalStateException("Unable to clear corrupted device identity"))
        }
        throw error
    }

    private data class EncryptedPayload(
        val iv: ByteArray,
        val ciphertext: ByteArray,
    )

    private companion object {
        const val PREFERENCES_FILE = "frame_archive_encrypted_identity"
        const val IV_KEY = "iv"
        const val CIPHERTEXT_KEY = "ciphertext"
        const val KEY_ALIAS = "frame_archive_identity_store_v1"
        const val ANDROID_KEY_STORE = "AndroidKeyStore"
        const val CIPHER_TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_SIZE_BITS = 256
        const val GCM_TAG_SIZE_BITS = 128
    }
}
