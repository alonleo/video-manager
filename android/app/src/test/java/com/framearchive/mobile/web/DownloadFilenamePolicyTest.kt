package com.framearchive.mobile.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DownloadFilenamePolicyTest {
    @Test
    fun keepsOrdinaryNamesAndRemovesDangerousCharacters() {
        assertEquals("movie.mp4", DownloadFilenamePolicy.sanitize("movie.mp4"))
        assertEquals("evil_name_.mp4", DownloadFilenamePolicy.sanitize("evil/name?.mp4"))
        assertFalse(DownloadFilenamePolicy.sanitize("safe\u202Egpj.exe").contains('\u202E'))
    }

    @Test
    fun dotNamesAndBlankNamesUseASafeFallback() {
        assertEquals("download", DownloadFilenamePolicy.sanitize("."))
        assertEquals("download", DownloadFilenamePolicy.sanitize(".."))
        assertEquals("download", DownloadFilenamePolicy.sanitize(" \u2066\u2069 "))
    }

    @Test
    fun truncatesByUtf8BytesAndCleansTrailingDotsAfterTruncation() {
        val multibyte = DownloadFilenamePolicy.sanitize("影".repeat(100) + ".mp4")
        assertTrue(multibyte.toByteArray(Charsets.UTF_8).size <= 120)

        val trailingDot = DownloadFilenamePolicy.sanitize("a".repeat(119) + ".suffix")
        assertEquals("a".repeat(119), trailingDot)
    }
}
