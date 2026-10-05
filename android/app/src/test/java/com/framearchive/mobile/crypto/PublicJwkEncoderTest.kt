package com.framearchive.mobile.crypto

import java.math.BigInteger
import java.security.interfaces.ECPublicKey
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PublicJwkEncoderTest {
    @Test
    fun `encodes known coordinates as exact 32 byte base64url values`() {
        val x = ByteArray(32) { it.toByte() }.also { it[0] = 1 }
        val y = ByteArray(32) { (255 - it).toByte() }

        val jwk = PublicJwkEncoder.encode(FakeECPublicKey(BigInteger(1, x), BigInteger(1, y)))

        assertEquals("EC", jwk.kty)
        assertEquals("P-256", jwk.crv)
        assertArrayEquals(x, decode(jwk.x))
        assertArrayEquals(y, decode(jwk.y))
        assertFalse(jwk.x.contains('='))
        assertFalse(jwk.y.contains('='))
        assertFalse(jwk.x.contains('+') || jwk.x.contains('/'))
        assertFalse(jwk.y.contains('+') || jwk.y.contains('/'))
    }

    @Test
    fun `removes BigInteger sign byte from a 33 byte coordinate`() {
        val coordinate = ByteArray(32) { 0xff.toByte() }

        val encoded = PublicJwkEncoder.encodeCoordinate(BigInteger(1, coordinate))

        assertArrayEquals(coordinate, decode(encoded))
    }

    @Test
    fun `left pads a 31 byte coordinate`() {
        val coordinate = ByteArray(31) { (it + 1).toByte() }

        val encoded = PublicJwkEncoder.encodeCoordinate(BigInteger(1, coordinate))

        assertArrayEquals(byteArrayOf(0) + coordinate, decode(encoded))
    }

    @Test
    fun `encodes zero as exactly 32 zero bytes`() {
        val encoded = PublicJwkEncoder.encodeCoordinate(BigInteger.ZERO)

        assertArrayEquals(ByteArray(32), decode(encoded))
    }

    @Test
    fun `rejects negative and oversized coordinates`() {
        assertThrows(IllegalArgumentException::class.java) {
            PublicJwkEncoder.encodeCoordinate(BigInteger.valueOf(-1))
        }
        assertThrows(IllegalArgumentException::class.java) {
            PublicJwkEncoder.encodeCoordinate(BigInteger.ONE.shiftLeft(256))
        }
    }

    @Test
    fun `uses URL safe alphabet without padding`() {
        val bytes = ByteArray(32) { 0xff.toByte() }
        val encoded = PublicJwkEncoder.encodeCoordinate(BigInteger(1, bytes))

        assertFalse(encoded.contains('='))
        assertFalse(encoded.contains('+'))
        assertFalse(encoded.contains('/'))
        assertEquals(43, encoded.length)
        assertArrayEquals(bytes, decode(encoded))
    }

    private fun decode(value: String): ByteArray = Base64.getUrlDecoder().decode(value)

    private class FakeECPublicKey(x: BigInteger, y: BigInteger) : ECPublicKey {
        private val point = ECPoint(x, y)

        override fun getW(): ECPoint = point
        override fun getParams(): ECParameterSpec? = null
        override fun getAlgorithm(): String = "EC"
        override fun getFormat(): String? = null
        override fun getEncoded(): ByteArray? = null
    }
}

class DeviceKeyCreationLockTest {
    @Test
    fun `serializes device key creation critical sections process wide`() {
        val executor = Executors.newFixedThreadPool(2)
        val firstEntered = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val secondEntered = CountDownLatch(1)

        try {
            val first = executor.submit {
                DeviceKeyStore.withCreationLock {
                    firstEntered.countDown()
                    check(releaseFirst.await(1, TimeUnit.SECONDS))
                }
            }
            assertTrue(firstEntered.await(1, TimeUnit.SECONDS))

            val second = executor.submit {
                DeviceKeyStore.withCreationLock {
                    secondEntered.countDown()
                }
            }

            assertFalse(secondEntered.await(100, TimeUnit.MILLISECONDS))
            releaseFirst.countDown()
            first.get(1, TimeUnit.SECONDS)
            second.get(1, TimeUnit.SECONDS)
            assertTrue(secondEntered.await(1, TimeUnit.SECONDS))
        } finally {
            releaseFirst.countDown()
            executor.shutdownNow()
        }
    }
}
