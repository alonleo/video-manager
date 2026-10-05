package com.framearchive.mobile.storage

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class GcmIvValidatorTest {
    @Test
    fun `accepts provider generated twelve byte IV`() {
        val iv = ByteArray(12) { it.toByte() }

        assertArrayEquals(iv, GcmIvValidator.requireValid(iv))
    }

    @Test
    fun `rejects missing or incorrectly sized provider IV`() {
        listOf(null, ByteArray(0), ByteArray(11), ByteArray(13)).forEach { iv ->
            assertThrows(IllegalStateException::class.java) {
                GcmIvValidator.requireValid(iv)
            }
        }
    }
}
