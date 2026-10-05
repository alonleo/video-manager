package com.framearchive.mobile.crypto

object DerSignatureConverter {
    private const val SEQUENCE_TAG = 0x30
    private const val INTEGER_TAG = 0x02
    private const val COORDINATE_SIZE = 32

    fun toP1363(der: ByteArray): ByteArray {
        val reader = DerReader(der)
        reader.expectTag(SEQUENCE_TAG, "ECDSA signature sequence")
        val sequenceLength = reader.readShortLength("ECDSA signature sequence")
        if (sequenceLength != reader.remaining) {
            throw IllegalArgumentException("DER sequence length does not match signature size")
        }

        val r = reader.readInteger("r")
        val s = reader.readInteger("s")
        if (reader.remaining != 0) {
            throw IllegalArgumentException("DER signature must contain exactly two integers")
        }

        return r + s
    }

    private class DerReader(private val bytes: ByteArray) {
        private var offset = 0

        val remaining: Int
            get() = bytes.size - offset

        fun expectTag(expected: Int, description: String) {
            val actual = readByte("Missing $description tag")
            if (actual != expected) {
                throw IllegalArgumentException("Invalid $description tag: expected 0x${expected.toString(16)}")
            }
        }

        fun readShortLength(description: String): Int {
            val length = readByte("Missing $description length")
            if (length >= 0x80) {
                throw IllegalArgumentException("$description must use DER short-form length")
            }
            if (length > remaining) {
                throw IllegalArgumentException("$description length exceeds available data")
            }
            return length
        }

        fun readInteger(name: String): ByteArray {
            expectTag(INTEGER_TAG, "$name integer")
            val length = readShortLength("$name integer")
            if (length == 0 || length > COORDINATE_SIZE + 1) {
                throw IllegalArgumentException("ECDSA $name integer must contain 1 to 33 bytes")
            }

            val value = readBytes(length, "$name integer")
            if (value[0].toInt() and 0x80 != 0) {
                throw IllegalArgumentException("ECDSA $name integer must not be negative")
            }

            val unsigned = if (value[0] == 0.toByte()) {
                if (value.size > 1 && value[1].toInt() and 0x80 == 0) {
                    throw IllegalArgumentException("ECDSA $name integer has a redundant leading zero")
                }
                value.copyOfRange(1, value.size)
            } else {
                value
            }

            if (unsigned.size > COORDINATE_SIZE) {
                throw IllegalArgumentException("ECDSA $name integer exceeds 32 bytes")
            }
            return ByteArray(COORDINATE_SIZE - unsigned.size) + unsigned
        }

        private fun readByte(error: String): Int {
            if (offset >= bytes.size) {
                throw IllegalArgumentException(error)
            }
            return bytes[offset++].toInt() and 0xff
        }

        private fun readBytes(length: Int, description: String): ByteArray {
            if (length > remaining) {
                throw IllegalArgumentException("$description length exceeds available data")
            }
            val result = bytes.copyOfRange(offset, offset + length)
            offset += length
            return result
        }
    }
}
