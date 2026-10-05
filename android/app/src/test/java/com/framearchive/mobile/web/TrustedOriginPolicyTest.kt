package com.framearchive.mobile.web

import com.framearchive.mobile.web.NavigationDecision.BLOCK
import com.framearchive.mobile.web.NavigationDecision.EXTERNAL_HTTPS
import com.framearchive.mobile.web.NavigationDecision.SAME_ORIGIN
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class TrustedOriginPolicyTest {
    private val policy = TrustedOriginPolicy("http://192.168.1.70:8420")

    @Test
    fun allowsOnlyExactNormalizedOriginInsideWebView() {
        assertEquals(SAME_ORIGIN, policy.decide("http://192.168.1.70:8420/videos/1"))
        assertEquals(SAME_ORIGIN, policy.decide("http://192.168.1.70:8420/?q=1#top"))
        assertEquals(BLOCK, policy.decide("http://192.168.1.71:8420/"))
        assertEquals(BLOCK, policy.decide("http://192.168.1.70:8421/"))
        assertEquals(EXTERNAL_HTTPS, policy.decide("https://192.168.1.70:8420/"))
    }

    @Test
    fun comparesDefaultAndExplicitPortsAsTheSameOrigin() {
        assertEquals(
            SAME_ORIGIN,
            TrustedOriginPolicy("https://Example.COM").decide("https://example.com:443/library"),
        )
        assertEquals(
            SAME_ORIGIN,
            TrustedOriginPolicy("http://example.com:80").decide("http://EXAMPLE.com/"),
        )
        assertEquals(
            SAME_ORIGIN,
            TrustedOriginPolicy("http://[fd00::1]:8420")
                .decide("http://[fd00:0:0:0:0:0:0:1]:8420/library"),
        )
        assertEquals(
            EXTERNAL_HTTPS,
            TrustedOriginPolicy("https://example.com").decide("https://example.com./library"),
        )
    }

    @Test
    fun sendsOnlyWellFormedExternalHttpsToTheSystem() {
        assertEquals(EXTERNAL_HTTPS, policy.decide("https://example.com/help"))
        assertEquals(BLOCK, policy.decide("http://example.com/steal"))
        assertEquals(BLOCK, policy.decide("https://user@example.com/help"))
        assertEquals(BLOCK, policy.decide("https://example.com:70000/help"))
    }

    @Test
    fun blocksHostDisguisesAndNonWebSchemes() {
        listOf(
            "http://192.168.1.70:8420.evil.example/",
            "http://192.168.1.70:8420@evil.example/",
            "intent://192.168.1.70:8420/#Intent;scheme=http;end",
            "file:///etc/passwd",
            "content://com.example/private",
            "javascript:alert(1)",
            "data:text/html,hello",
            "blob:http://192.168.1.70:8420/id",
            "not a url",
        ).forEach { url -> assertEquals(url, BLOCK, policy.decide(url)) }
    }

    @Test
    fun trustedOriginMustBeAPlainHttpOrHttpsOrigin() {
        listOf(
            "https://user@example.com",
            "https://example.com/path",
            "https://example.com?query",
            "https://example.com#fragment",
            "https://example.com:0",
            "file:///tmp/site",
        ).forEach { origin ->
            assertThrows(origin, IllegalArgumentException::class.java) { TrustedOriginPolicy(origin) }
        }
    }
}
