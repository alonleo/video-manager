package com.framearchive.mobile.crypto

import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECGenParameterSpec
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

class P256KeyValidatorTest {
    @Test
    fun `accepts a generated secp256r1 public key`() {
        assertTrue(P256KeyValidator.isP256(generateEcKey("secp256r1")))
    }

    @Test
    fun `rejects a generated P-384 public key`() {
        assertFalse(P256KeyValidator.isP256(generateEcKey("secp384r1")))
    }

    @Test
    fun `rejects different parameters with the same 256 bit field size`() {
        val validKey = generateEcKey("secp256r1")
        val parameters = validKey.params
        val changedGenerator = ECPoint(
            parameters.generator.affineX.add(BigInteger.ONE),
            parameters.generator.affineY,
        )
        val changedParameters = ECParameterSpec(
            parameters.curve,
            changedGenerator,
            parameters.order,
            parameters.cofactor,
        )

        assertFalse(P256KeyValidator.isP256(keyWithParameters(validKey, changedParameters)))
    }

    private fun generateEcKey(curve: String): ECPublicKey {
        val generator = KeyPairGenerator.getInstance("EC")
        generator.initialize(ECGenParameterSpec(curve))
        return generator.generateKeyPair().public as ECPublicKey
    }

    private fun keyWithParameters(key: ECPublicKey, parameters: ECParameterSpec): ECPublicKey =
        object : ECPublicKey {
            override fun getW(): ECPoint = key.w
            override fun getParams(): ECParameterSpec = parameters
            override fun getAlgorithm(): String = key.algorithm
            override fun getFormat(): String? = key.format
            override fun getEncoded(): ByteArray = key.encoded
        }
}

class DeviceKeyCreationLockPrimitiveTest {
    @Test
    fun `shared lock primitive serializes process-wide critical sections`() {
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
