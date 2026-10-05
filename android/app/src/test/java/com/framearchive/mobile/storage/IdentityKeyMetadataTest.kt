package com.framearchive.mobile.storage

import android.security.keystore.KeyProperties
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IdentityKeyMetadataTest {
    @Test
    fun `accepts exact AES 256 GCM storage key policy`() {
        assertTrue(isValidIdentityStoreKey(validMetadata()))
    }

    @Test
    fun `rejects AES 128 key`() {
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(keySizeBits = 128)))
    }

    @Test
    fun `rejects key missing encryption or decryption purpose`() {
        assertFalse(
            isValidIdentityStoreKey(validMetadata().copy(purposes = KeyProperties.PURPOSE_ENCRYPT)),
        )
        assertFalse(
            isValidIdentityStoreKey(validMetadata().copy(purposes = KeyProperties.PURPOSE_DECRYPT)),
        )
        assertFalse(
            isValidIdentityStoreKey(
                validMetadata().copy(
                    purposes = KeyProperties.PURPOSE_ENCRYPT or
                        KeyProperties.PURPOSE_DECRYPT or
                        KeyProperties.PURPOSE_SIGN,
                ),
            ),
        )
    }

    @Test
    fun `rejects wrong or additional block modes`() {
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(blockModes = setOf("CBC"))))
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(blockModes = setOf("GCM", "CBC"))))
    }

    @Test
    fun `rejects wrong or additional encryption padding`() {
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(encryptionPaddings = setOf("PKCS7Padding"))))
        assertFalse(
            isValidIdentityStoreKey(
                validMetadata().copy(encryptionPaddings = setOf("NoPadding", "PKCS7Padding")),
            ),
        )
    }

    @Test
    fun `rejects key requiring user authentication`() {
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(userAuthenticationRequired = true)))
    }

    @Test
    fun `rejects key with randomized encryption disabled`() {
        assertFalse(isValidIdentityStoreKey(validMetadata().copy(randomizedEncryptionRequired = false)))
    }

    private fun validMetadata() = IdentityKeyMetadata(
        keySizeBits = 256,
        purposes = KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        blockModes = setOf(KeyProperties.BLOCK_MODE_GCM),
        encryptionPaddings = setOf(KeyProperties.ENCRYPTION_PADDING_NONE),
        userAuthenticationRequired = false,
        randomizedEncryptionRequired = true,
    )
}
