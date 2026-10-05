package com.framearchive.mobile.storage

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyInfo
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.InvalidAlgorithmParameterException
import java.security.KeyStore
import java.security.KeyStoreException
import java.security.ProviderException
import java.security.UnrecoverableKeyException
import java.util.Collections
import java.util.Base64
import java.util.IdentityHashMap
import javax.crypto.BadPaddingException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.SecretKeyFactory
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

class IdentityStoreUnavailable(
    message: String,
    cause: Throwable? = null,
) : IllegalStateException(message, cause)

internal class InvalidIdentityStoreKeyException(
    message: String,
    cause: Throwable? = null,
) : GeneralSecurityException(message, cause)

internal enum class IdentityReadFailure {
    CIPHERTEXT_CORRUPTED,
    KEY_PERMANENTLY_INVALIDATED,
    KEY_POLICY_INVALID,
    UNAVAILABLE,
}

internal fun classifyIdentityReadSecurityFailure(error: Throwable): IdentityReadFailure {
    if (error.hasCauseOfType<KeyPermanentlyInvalidatedException>()) {
        return IdentityReadFailure.KEY_PERMANENTLY_INVALIDATED
    }
    if (error.hasCauseOfType<InvalidIdentityStoreKeyException>()) {
        return IdentityReadFailure.KEY_POLICY_INVALID
    }
    if (error.hasCauseOfType<BadPaddingException>()) {
        return IdentityReadFailure.CIPHERTEXT_CORRUPTED
    }
    return IdentityReadFailure.UNAVAILABLE
}

internal fun shouldRetryKeyRecovery(failure: IdentityReadFailure, retryCount: Int): Boolean =
    retryCount == 0 &&
        (failure == IdentityReadFailure.KEY_PERMANENTLY_INVALIDATED ||
            failure == IdentityReadFailure.KEY_POLICY_INVALID)

internal data class IdentityKeyMetadata(
    val keySizeBits: Int,
    val purposes: Int,
    val blockModes: Set<String>,
    val encryptionPaddings: Set<String>,
    val userAuthenticationRequired: Boolean,
    val randomizedEncryptionRequired: Boolean,
)

internal fun isValidIdentityStoreKey(metadata: IdentityKeyMetadata): Boolean =
    metadata.keySizeBits == IDENTITY_KEY_SIZE_BITS &&
        metadata.purposes == IDENTITY_KEY_PURPOSES &&
        metadata.blockModes == setOf(KeyProperties.BLOCK_MODE_GCM) &&
        metadata.encryptionPaddings == setOf(KeyProperties.ENCRYPTION_PADDING_NONE) &&
        !metadata.userAuthenticationRequired &&
        metadata.randomizedEncryptionRequired

private const val IDENTITY_KEY_SIZE_BITS = 256
private val IDENTITY_KEY_PURPOSES = KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT

private inline fun <reified T : Throwable> Throwable.hasCauseOfType(): Boolean {
    val visited = Collections.newSetFromMap(IdentityHashMap<Throwable, Boolean>())
    var current: Throwable? = this
    while (current != null && visited.add(current)) {
        if (current is T) return true
        current = current.cause
    }
    return false
}

internal object GcmIvValidator {
    fun requireValid(iv: ByteArray?): ByteArray =
        iv?.takeIf { it.size == GCM_IV_SIZE_BYTES }
            ?: throw IllegalStateException("Android Keystore did not generate a valid GCM IV")
}

private const val GCM_IV_SIZE_BYTES = 12

class EncryptedIdentityStore(context: Context) : IdentityStore {
    private val preferences: SharedPreferences =
        (context.applicationContext ?: context).getSharedPreferences(PREFERENCES_FILE, Context.MODE_PRIVATE)

    override fun read(): DeviceIdentity? = synchronized(STORE_LOCK) {
        readLocked()
    }

    private fun readLocked(): DeviceIdentity? {
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
            handleReadCryptoFailure(error)
        } catch (error: IOException) {
            handleReadCryptoFailure(error)
        } catch (error: ProviderException) {
            handleReadCryptoFailure(error)
        } catch (error: IllegalArgumentException) {
            handleReadCryptoFailure(error)
        }

        return try {
            IdentityCodec.decode(String(plaintext, StandardCharsets.UTF_8))
        } catch (error: IdentityCodecException) {
            clearCorruptedAndThrow("Stored device identity payload is invalid", error)
        }
    }

    override fun save(identity: DeviceIdentity) = synchronized(STORE_LOCK) {
        saveLocked(identity)
    }

    private fun saveLocked(identity: DeviceIdentity) {
        val plaintext = IdentityCodec.encode(identity).toByteArray(StandardCharsets.UTF_8)
        val encrypted = encryptWithKeyRecovery(plaintext)

        val committed = preferences.edit()
            .clear()
            .putString(IV_KEY, Base64.getEncoder().encodeToString(encrypted.iv))
            .putString(CIPHERTEXT_KEY, Base64.getEncoder().encodeToString(encrypted.ciphertext))
            .commit()
        if (!committed) {
            throw IllegalStateException("Unable to persist encrypted device identity")
        }
    }

    override fun clear() = synchronized(STORE_LOCK) {
        if (!preferences.edit().clear().commit()) {
            throw IllegalStateException("Unable to clear encrypted device identity")
        }
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun existingKey(): SecretKey? {
        val store = keyStore()
        if (!store.containsAlias(KEY_ALIAS)) return null
        val key = store.getKey(KEY_ALIAS, null)
        if (key !is SecretKey || !key.algorithm.equals(KeyProperties.KEY_ALGORITHM_AES, ignoreCase = true)) {
            throw InvalidIdentityStoreKeyException("Device identity alias is not an AES secret key")
        }
        val metadata = identityKeyMetadata(key)
        if (!isValidIdentityStoreKey(metadata)) {
            throw InvalidIdentityStoreKeyException(
                "Device identity key parameters do not match the required policy",
            )
        }
        return key
    }

    @Throws(GeneralSecurityException::class)
    private fun identityKeyMetadata(key: SecretKey): IdentityKeyMetadata {
        val keyInfo = SecretKeyFactory.getInstance(key.algorithm, ANDROID_KEY_STORE)
            .getKeySpec(key, KeyInfo::class.java) as? KeyInfo
            ?: throw KeyStoreException("Android Keystore did not provide key metadata")
        val keyInfoMetadata = IdentityKeyMetadata(
            keySizeBits = keyInfo.keySize,
            purposes = keyInfo.purposes,
            blockModes = keyInfo.blockModes?.toSet().orEmpty(),
            encryptionPaddings = keyInfo.encryptionPaddings?.toSet().orEmpty(),
            userAuthenticationRequired = keyInfo.isUserAuthenticationRequired,
            randomizedEncryptionRequired = true,
        )
        if (!isValidIdentityStoreKey(keyInfoMetadata)) {
            throw InvalidIdentityStoreKeyException(
                "Device identity key metadata does not match the required policy",
            )
        }
        return keyInfoMetadata.copy(randomizedEncryptionRequired = rejectsCallerProvidedIv(key))
    }

    @Throws(GeneralSecurityException::class)
    private fun rejectsCallerProvidedIv(key: SecretKey): Boolean = try {
        // KeyInfo has no public randomized-encryption getter (including API 36). A valid caller-IV
        // init must therefore be rejected to prove this existing key still enforces provider IVs.
        // The probe intentionally never encrypts data or persists its IV.
        Cipher.getInstance(CIPHER_TRANSFORMATION).run {
            init(
                Cipher.ENCRYPT_MODE,
                key,
                GCMParameterSpec(GCM_TAG_SIZE_BITS, ByteArray(GCM_IV_SIZE_BYTES)),
            )
        }
        false
    } catch (_: InvalidAlgorithmParameterException) {
        true
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun getOrCreateKey(): SecretKey = synchronized(STORE_LOCK) {
        existingKey()?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE)
        val parameters = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setKeySize(IDENTITY_KEY_SIZE_BITS)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setUserAuthenticationRequired(false)
            .build()
        generator.init(parameters)
        generator.generateKey()
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply {
        load(null)
    }

    private fun encryptWithKeyRecovery(plaintext: ByteArray): EncryptedPayload {
        var retryCount = 0
        while (true) {
            try {
                return Cipher.getInstance(CIPHER_TRANSFORMATION).run {
                    init(Cipher.ENCRYPT_MODE, getOrCreateKey())
                    val providerIv = GcmIvValidator.requireValid(iv)
                    EncryptedPayload(providerIv, doFinal(plaintext))
                }
            } catch (error: GeneralSecurityException) {
                recoverInvalidKeyOrThrow(error, retryCount)
            } catch (error: IOException) {
                recoverInvalidKeyOrThrow(error, retryCount)
            } catch (error: ProviderException) {
                recoverInvalidKeyOrThrow(error, retryCount)
            } catch (error: IllegalArgumentException) {
                recoverInvalidKeyOrThrow(error, retryCount)
            }
            retryCount += 1
        }
    }

    private fun recoverInvalidKeyOrThrow(error: Throwable, retryCount: Int) {
        val failure = classifyIdentityReadSecurityFailure(error)
        if (!shouldRetryKeyRecovery(failure, retryCount)) {
            throw IdentityStoreUnavailable("Unable to encrypt device identity", error)
        }

        try {
            deleteStorageKey()
        } catch (deleteError: GeneralSecurityException) {
            throw keyRecoveryUnavailable(error, deleteError)
        } catch (deleteError: IOException) {
            throw keyRecoveryUnavailable(error, deleteError)
        } catch (deleteError: ProviderException) {
            throw keyRecoveryUnavailable(error, deleteError)
        }
    }

    private fun keyRecoveryUnavailable(invalidation: Throwable, deleteError: Throwable): IdentityStoreUnavailable =
        IdentityStoreUnavailable("Unable to replace invalid device identity key", deleteError).also {
            it.addSuppressed(invalidation)
        }

    private fun handleReadCryptoFailure(error: Throwable): Nothing =
        when (classifyIdentityReadSecurityFailure(error)) {
            IdentityReadFailure.CIPHERTEXT_CORRUPTED ->
                clearCorruptedAndThrow("Stored device identity authentication failed", error)
            IdentityReadFailure.KEY_PERMANENTLY_INVALIDATED ->
                clearInvalidKeyAndThrow("Stored device identity key was permanently invalidated", error)
            IdentityReadFailure.KEY_POLICY_INVALID ->
                clearInvalidKeyAndThrow("Stored device identity key has invalid parameters", error)
            IdentityReadFailure.UNAVAILABLE ->
                throw IdentityStoreUnavailable("Encrypted device identity is temporarily unavailable", error)
        }

    private fun clearInvalidKeyAndThrow(message: String, cause: Throwable): Nothing {
        val error = IdentityStoreCorrupted(message, cause)
        try {
            deleteStorageKey()
        } catch (cleanupError: GeneralSecurityException) {
            error.addSuppressed(cleanupError)
        } catch (cleanupError: IOException) {
            error.addSuppressed(cleanupError)
        } catch (cleanupError: ProviderException) {
            error.addSuppressed(cleanupError)
        }
        if (!preferences.edit().clear().commit()) {
            error.addSuppressed(IllegalStateException("Unable to clear identity for an invalid storage key"))
        }
        throw error
    }

    @Throws(GeneralSecurityException::class, IOException::class)
    private fun deleteStorageKey() {
        val store = keyStore()
        if (store.containsAlias(KEY_ALIAS)) {
            store.deleteEntry(KEY_ALIAS)
        }
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
        val STORE_LOCK = Any()
        const val PREFERENCES_FILE = "frame_archive_encrypted_identity"
        const val IV_KEY = "iv"
        const val CIPHERTEXT_KEY = "ciphertext"
        const val KEY_ALIAS = "frame_archive_identity_store_v1"
        const val ANDROID_KEY_STORE = "AndroidKeyStore"
        const val CIPHER_TRANSFORMATION = "AES/GCM/NoPadding"
        const val GCM_TAG_SIZE_BITS = 128
    }
}
