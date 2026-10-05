package com.framearchive.mobile.web

import java.io.ByteArrayOutputStream
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SameOriginDownloadFetcherTest {
    @Test
    fun followsSameOriginRedirectAndSendsCookieOnlyToValidatedRequests() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "/final"))
            server.enqueue(MockResponse().setBody("video"))
            val origin = server.url("/").toString().removeSuffix("/")
            val output = ByteArrayOutputStream()

            val result = SameOriginDownloadFetcher(
                OkHttpClient(),
                TrustedOriginPolicy(origin),
                "frame_device_session=secret",
            ).fetch(server.url("/start").toString(), output)

            assertTrue(result is DownloadFetchResult.Success)
            assertEquals("video", output.toString(Charsets.UTF_8.name()))
            assertEquals("frame_device_session=secret", server.takeRequest().getHeader("Cookie"))
            assertEquals("frame_device_session=secret", server.takeRequest().getHeader("Cookie"))
        }
    }

    @Test
    fun rejectsCrossOriginRedirectBeforeTheOtherOriginReceivesAnyRequest() {
        MockWebServer().use { trusted ->
            MockWebServer().use { attacker ->
                trusted.enqueue(
                    MockResponse().setResponseCode(302).addHeader("Location", attacker.url("/steal")),
                )
                val origin = trusted.url("/").toString().removeSuffix("/")
                val output = ByteArrayOutputStream()

                val result = SameOriginDownloadFetcher(
                    OkHttpClient(),
                    TrustedOriginPolicy(origin),
                    "frame_device_session=secret",
                ).fetch(trusted.url("/start").toString(), output)

                assertTrue(result is DownloadFetchResult.Rejected)
                assertEquals(1, trusted.requestCount)
                assertEquals(0, attacker.requestCount)
                assertEquals(0, output.size())
                assertFalse(result.toString().contains("secret"))
            }
        }
    }
}
