package com.framearchive.mobile.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
}
