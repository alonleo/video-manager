package com.framearchive.mobile.web

import java.io.ByteArrayOutputStream
import java.nio.file.Files
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SameOriginDownloadFetcherTest {
    @Test
    fun cancelledBeforeFetchNeverCreatesARequest() {
        MockWebServer().use { server ->
            val origin = server.url("/").toString().removeSuffix("/")
            val output = ByteArrayOutputStream()
            val fetcher = SameOriginDownloadFetcher(
                OkHttpClient(),
                TrustedOriginPolicy(origin),
                "frame_device_session=secret",
            )

            fetcher.cancel()
            val result = fetcher.fetch(server.url("/start").toString(), output)

            assertTrue(result is DownloadFetchResult.Cancelled)
            assertEquals(0, server.requestCount)
            assertEquals(0, output.size())
        }
    }

    @Test
    fun cancellationAfterCallRegistrationPreventsExecute() {
        MockWebServer().use { server ->
            val origin = server.url("/").toString().removeSuffix("/")
            lateinit var fetcher: SameOriginDownloadFetcher
            fetcher = SameOriginDownloadFetcher(
                OkHttpClient(),
                TrustedOriginPolicy(origin),
                "frame_device_session=secret",
                DownloadFetchHooks(afterCallRegistered = { fetcher.cancel() }),
            )
            val output = ByteArrayOutputStream()

            val result = fetcher.fetch(server.url("/start").toString(), output)

            assertTrue(result is DownloadFetchResult.Cancelled)
            assertEquals(0, server.requestCount)
            assertEquals(0, output.size())
        }
    }

    @Test
    fun cancellationBetweenSameOriginRedirectsStopsTheNextRequest() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "/second"))
            server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "/final"))
            server.enqueue(MockResponse().setBody("must-not-download"))
            val origin = server.url("/").toString().removeSuffix("/")
            lateinit var fetcher: SameOriginDownloadFetcher
            fetcher = SameOriginDownloadFetcher(
                OkHttpClient(),
                TrustedOriginPolicy(origin),
                "frame_device_session=secret",
                DownloadFetchHooks(beforeRedirect = { redirectCount ->
                    if (redirectCount == 1) fetcher.cancel()
                }),
            )
            val output = ByteArrayOutputStream()

            val result = fetcher.fetch(server.url("/first").toString(), output)

            assertTrue(result is DownloadFetchResult.Cancelled)
            assertEquals(2, server.requestCount)
            assertEquals("frame_device_session=secret", server.takeRequest().getHeader("Cookie"))
            assertEquals("frame_device_session=secret", server.takeRequest().getHeader("Cookie"))
            assertEquals(0, output.size())
        }
    }

    @Test
    fun cancellationDuringResponseReadDeletesThePartFile() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("a".repeat(32_000)))
            val origin = server.url("/").toString().removeSuffix("/")
            lateinit var fetcher: SameOriginDownloadFetcher
            fetcher = SameOriginDownloadFetcher(
                OkHttpClient(),
                TrustedOriginPolicy(origin),
                "frame_device_session=secret",
                DownloadFetchHooks(afterChunkRead = { fetcher.cancel() }),
            )
            val directory = Files.createTempDirectory("frame-download-test").toFile()

            try {
                val result = SecureDownloadFileWriter(fetcher).download(
                    server.url("/large").toString(),
                    directory,
                )

                assertTrue(result is DownloadFileResult.Cancelled)
                assertEquals(1, server.requestCount)
                assertTrue(directory.listFiles().orEmpty().isEmpty())
            } finally {
                directory.deleteRecursively()
            }
        }
    }

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
