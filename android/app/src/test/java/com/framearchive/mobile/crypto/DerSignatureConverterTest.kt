package com.framearchive.mobile.crypto

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class DerSignatureConverterTest {
    @Test
    fun `pads 31 byte r and preserves 32 byte s`() {
        val r = ByteArray(31) { (it + 1).toByte() }
        val s = ByteArray(32) { (it + 33).toByte() }.also { it[0] = 0x01 }

        val result = DerSignatureConverter.toP1363(signature(r, s))

        assertEquals(64, result.size)
        assertArrayEquals(byteArrayOf(0) + r, result.copyOfRange(0, 32))
        assertArrayEquals(s, result.copyOfRange(32, 64))
    }

    @Test
    fun `preserves 32 byte r and pads 31 byte s`() {
        val r = ByteArray(32) { (it + 1).toByte() }
        val s = ByteArray(31) { (it + 65).toByte() }

        val result = DerSignatureConverter.toP1363(signature(r, s))

        assertEquals(64, result.size)
        assertArrayEquals(r, result.copyOfRange(0, 32))
        assertArrayEquals(byteArrayOf(0) + s, result.copyOfRange(32, 64))
    }

    @Test
    fun `removes required sign byte from either 33 byte integer`() {
        val r = byteArrayOf(0) + ByteArray(32) { 0x80.toByte() }
        val s = byteArrayOf(0) + ByteArray(32) { 0xff.toByte() }

        val result = DerSignatureConverter.toP1363(signature(r, s))

        assertArrayEquals(r.copyOfRange(1, 33), result.copyOfRange(0, 32))
        assertArrayEquals(s.copyOfRange(1, 33), result.copyOfRange(32, 64))
    }

    @Test
    fun `rejects negative integers without sign protection`() {
        assertInvalid(signature(byteArrayOf(0x80.toByte()), byteArrayOf(1)))
        assertInvalid(signature(byteArrayOf(1), byteArrayOf(0xff.toByte())))
    }

    @Test
    fun `rejects redundant leading zero`() {
        assertInvalid(signature(byteArrayOf(0, 0x7f), byteArrayOf(1)))
        assertInvalid(signature(byteArrayOf(1), byteArrayOf(0, 1)))
    }

    @Test
    fun `rejects invalid integer sizes and malformed 33 byte values`() {
        assertInvalid(signature(byteArrayOf(), byteArrayOf(1)))
        assertInvalid(signature(ByteArray(34) { 1 }, byteArrayOf(1)))
        assertInvalid(signature(ByteArray(33) { 1 }, byteArrayOf(1)))
        assertInvalid(signature(byteArrayOf(1), byteArrayOf()))
        assertInvalid(signature(byteArrayOf(1), ByteArray(34) { 1 }))
        assertInvalid(signature(byteArrayOf(1), ByteArray(33) { 1 }))
    }

    @Test
    fun `rejects incorrect sequence and integer tags`() {
        val valid = signature(byteArrayOf(1), byteArrayOf(2))
        assertInvalid(valid.copyOf().also { it[0] = 0x31 })
        assertInvalid(valid.copyOf().also { it[2] = 0x03 })
        assertInvalid(valid.copyOf().also { it[5] = 0x03 })
    }

    @Test
    fun `rejects truncated overflowing and trailing data`() {
        val valid = signature(byteArrayOf(1), byteArrayOf(2))
        assertInvalid(valid.copyOf(valid.size - 1))
        assertInvalid(valid.copyOf().also { it[1] = (it[1] + 1).toByte() })
        assertInvalid(valid + byteArrayOf(0))
        assertInvalid(valid.copyOf().also { it[3] = 9 })
        assertInvalid(valid.copyOf().also { it[6] = 9 })
    }

    @Test
    fun `rejects a third integer in the sequence`() {
        val r = integer(byteArrayOf(1))
        val s = integer(byteArrayOf(2))
        val third = integer(byteArrayOf(3))
        assertInvalid(byteArrayOf(0x30, (r.size + s.size + third.size).toByte()) + r + s + third)
    }

    @Test
    fun `rejects DER long form lengths for P-256 signatures`() {
        val valid = signature(ByteArray(32) { 1 }, ByteArray(32) { 2 })
        val longSequence = byteArrayOf(0x30, 0x81.toByte(), valid[1]) + valid.copyOfRange(2, valid.size)
        assertInvalid(longSequence)

        val longInteger = byteArrayOf(0x30, (valid[1] + 1).toByte(), 0x02, 0x81.toByte(), 32) +
            ByteArray(32) { 1 } + integer(ByteArray(32) { 2 })
        assertInvalid(longInteger)
    }

    private fun assertInvalid(der: ByteArray) {
        assertThrows(IllegalArgumentException::class.java) {
            DerSignatureConverter.toP1363(der)
        }
    }

    private fun signature(r: ByteArray, s: ByteArray): ByteArray {
        val content = integer(r) + integer(s)
        require(content.size < 128)
        return byteArrayOf(0x30, content.size.toByte()) + content
    }

    private fun integer(value: ByteArray): ByteArray {
        require(value.size < 128)
        return byteArrayOf(0x02, value.size.toByte()) + value
    }
}
