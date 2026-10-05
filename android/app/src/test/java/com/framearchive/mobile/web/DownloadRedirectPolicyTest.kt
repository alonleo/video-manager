package com.framearchive.mobile.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DownloadRedirectPolicyTest {
    private val policy = DownloadRedirectPolicy(TrustedOriginPolicy("https://video.example.com:8443"))

    @Test
    fun resolvesSameOriginRelativeAndAbsoluteRedirects() {
        assertEquals(
            "https://video.example.com:8443/files/two.mp4",
            policy.next(
                currentUrl = "https://video.example.com:8443/files/one.mp4",
                location = "two.mp4",
                visited = setOf("https://video.example.com:8443/files/one.mp4"),
                redirectCount = 0,
            ),
        )
        assertEquals(
            "https://video.example.com:8443/final.mp4",
            policy.next(
                "https://video.example.com:8443/start",
                "https://video.example.com:8443/final.mp4",
                setOf("https://video.example.com:8443/start"),
                1,
            ),
        )
    }

    @Test
    fun rejectsCrossOriginDowngradePortChangeLoopsAndExcessRedirects() {
        val current = "https://video.example.com:8443/start"
        val visited = setOf(current, "https://video.example.com:8443/already")
        listOf(
            "https://evil.example/file",
            "http://video.example.com:8443/file",
            "https://video.example.com:9443/file",
            "https://video.example.com:8443/already",
            "javascript:alert(1)",
            "%zz",
            "\r\nhttps://video.example.com:8443/file",
        ).forEach { location ->
            assertNull(location, policy.next(current, location, visited, 1))
        }
        assertNull(
            policy.next(
                current,
                "/too-many",
                visited,
                DownloadRedirectPolicy.MAX_REDIRECTS,
            ),
        )
        assertNull(policy.next(current, null, visited, 1))
        assertNull(policy.next(current, "", visited, 1))
    }
}
