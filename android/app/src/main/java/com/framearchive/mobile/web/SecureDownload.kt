package com.framearchive.mobile.web

import java.io.IOException
import java.io.OutputStream
import java.io.File
import java.net.URI
import java.net.URLDecoder
import java.nio.file.Files
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import okhttp3.Call
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request

internal class DownloadRedirectPolicy(
    private val trustedOrigin: TrustedOriginPolicy,
) {
    fun next(
        currentUrl: String,
        location: String?,
        visited: Set<String>,
        redirectCount: Int,
    ): String? {
        if (redirectCount >= MAX_REDIRECTS || location.isNullOrBlank()) return null
        if (location.any { it.code <= 0x20 || it.code == 0x7f }) return null
        val resolved = try {
            URI(currentUrl).resolve(URI(location)).normalize().toASCIIString()
        } catch (_: Exception) {
            return null
        }
        if (trustedOrigin.decide(resolved) != NavigationDecision.SAME_ORIGIN) return null
        if (resolved in visited) return null
        return resolved
    }

    companion object {
        const val MAX_REDIRECTS = 5
    }
}

internal object DownloadFilenamePolicy {
    private val forbidden = Regex("[\\u0000-\\u001f\\u007f/\\\\:*?\"<>|]")
    private val bidiControls = Regex("[\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")

    fun sanitize(candidate: String?): String {
        val cleaned = candidate.orEmpty()
            .replace(bidiControls, "")
            .replace(forbidden, "_")
            .trim()
            .trimEnd('.', ' ')
        if (cleaned.isBlank() || cleaned == "." || cleaned == "..") return "download"
        val truncated = utf8Prefix(cleaned, MAX_FILENAME_BYTES).trimEnd('.', ' ')
        return truncated.takeUnless { it.isBlank() || it == "." || it == ".." } ?: "download"
    }

    fun choose(finalUrl: String, contentDisposition: String?): String {
        val fromHeader = contentDisposition
            ?.split(';')
            ?.map(String::trim)
            ?.firstNotNullOfOrNull { part ->
                when {
                    part.startsWith("filename*=UTF-8''", ignoreCase = true) ->
                        decode(part.substringAfter("''"))
                    part.startsWith("filename=", ignoreCase = true) ->
                        part.substringAfter('=').trim().trim('"').takeIf(String::isNotBlank)
                    else -> null
                }
            }
        val fromUrl = try {
            URI(finalUrl).rawPath.substringAfterLast('/').takeIf(String::isNotBlank)?.let(::decode)
        } catch (_: Exception) {
            null
        }
        return sanitize(fromHeader ?: fromUrl)
    }

    private fun decode(value: String): String? = try {
        URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8.name())
    } catch (_: IllegalArgumentException) {
        null
    }

    private fun utf8Prefix(value: String, maxBytes: Int): String {
        val result = StringBuilder()
        var byteCount = 0
        var index = 0
        while (index < value.length) {
            val codePoint = value.codePointAt(index)
            val text = String(Character.toChars(codePoint))
            val bytes = text.toByteArray(StandardCharsets.UTF_8).size
            if (byteCount + bytes > maxBytes) break
            result.append(text)
            byteCount += bytes
            index += Character.charCount(codePoint)
        }
        return result.toString()
    }

    private const val MAX_FILENAME_BYTES = 120
}

internal sealed interface DownloadFetchResult {
    data class Success(
        val filename: String,
        val contentType: String?,
        val bytesWritten: Long,
    ) : DownloadFetchResult

    data class Rejected(val reason: String) : DownloadFetchResult
    data class Failed(val reason: String) : DownloadFetchResult
    data object Cancelled : DownloadFetchResult
}

internal data class DownloadFetchHooks(
    val afterCallRegistered: () -> Unit = {},
    val beforeRedirect: (redirectCount: Int) -> Unit = {},
    val afterChunkRead: () -> Unit = {},
)

internal class SameOriginDownloadFetcher(
    client: OkHttpClient,
    private val trustedOrigin: TrustedOriginPolicy,
    private val sessionCookiePair: String,
    private val hooks: DownloadFetchHooks = DownloadFetchHooks(),
) {
    private val client = client.newBuilder()
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .cookieJar(CookieJar.NO_COOKIES)
        .build()
    private val activeCall = AtomicReference<Call?>()
    private val cancelled = AtomicBoolean(false)

    init {
        require(SessionCookiePolicy.isValidCookiePair(sessionCookiePair))
    }

    fun fetch(initialUrl: String, output: OutputStream): DownloadFetchResult {
        if (cancelled.get()) return DownloadFetchResult.Cancelled
        if (trustedOrigin.decide(initialUrl) != NavigationDecision.SAME_ORIGIN) {
            return DownloadFetchResult.Rejected("Download URL is not same-origin")
        }
        val redirects = DownloadRedirectPolicy(trustedOrigin)
        val visited = linkedSetOf<String>()
        var currentUrl = initialUrl
        var redirectCount = 0
        while (true) {
            if (cancelled.get()) return DownloadFetchResult.Cancelled
            if (!visited.add(currentUrl)) return DownloadFetchResult.Rejected("Redirect loop")
            val request = Request.Builder()
                .url(currentUrl)
                .get()
                .header("Cookie", sessionCookiePair)
                .build()
            val call = client.newCall(request)
            activeCall.set(call)
            hooks.afterCallRegistered()
            if (cancelled.get()) {
                call.cancel()
                activeCall.compareAndSet(call, null)
                return DownloadFetchResult.Cancelled
            }
            val response = try {
                call.execute()
            } catch (error: IOException) {
                activeCall.compareAndSet(call, null)
                if (cancelled.get()) return DownloadFetchResult.Cancelled
                return DownloadFetchResult.Failed(error.message ?: "Network error")
            }
            try {
                if (cancelled.get()) return DownloadFetchResult.Cancelled
                response.use { received ->
                    if (received.code in REDIRECT_CODES) {
                        val next = redirects.next(
                            currentUrl,
                            received.header("Location"),
                            visited,
                            redirectCount,
                        ) ?: return DownloadFetchResult.Rejected("Unsafe redirect")
                        hooks.beforeRedirect(redirectCount)
                        if (cancelled.get()) return DownloadFetchResult.Cancelled
                        currentUrl = next
                        redirectCount += 1
                        continue
                    }
                    if (!received.isSuccessful) {
                        return DownloadFetchResult.Failed("Download returned HTTP ${received.code}")
                    }
                    val body = received.body ?: return DownloadFetchResult.Failed("Download body is missing")
                    if (body.contentLength() > MAX_DOWNLOAD_BYTES) {
                        return DownloadFetchResult.Rejected("Download is too large")
                    }
                    val filename = DownloadFilenamePolicy.choose(
                        currentUrl,
                        received.header("Content-Disposition"),
                    )
                    var total = 0L
                    return try {
                        body.byteStream().use { input ->
                            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                            while (true) {
                                if (cancelled.get()) return DownloadFetchResult.Cancelled
                                val read = input.read(buffer)
                                if (read < 0) break
                                hooks.afterChunkRead()
                                if (cancelled.get()) return DownloadFetchResult.Cancelled
                                total += read
                                if (total > MAX_DOWNLOAD_BYTES) {
                                    return DownloadFetchResult.Rejected("Download is too large")
                                }
                                output.write(buffer, 0, read)
                            }
                            output.flush()
                        }
                        if (cancelled.get()) return DownloadFetchResult.Cancelled
                        DownloadFetchResult.Success(filename, body.contentType()?.toString(), total)
                    } catch (error: IOException) {
                        if (cancelled.get()) return DownloadFetchResult.Cancelled
                        DownloadFetchResult.Failed(error.message ?: "Unable to write download")
                    }
                }
            } finally {
                activeCall.compareAndSet(call, null)
            }
        }
    }

    fun cancel() {
        cancelled.set(true)
        activeCall.getAndSet(null)?.cancel()
    }

    fun isCancelled(): Boolean = cancelled.get()

    private companion object {
        val REDIRECT_CODES = setOf(301, 302, 303, 307, 308)
        const val MAX_DOWNLOAD_BYTES = 20L * 1024L * 1024L * 1024L
    }
}

internal sealed interface DownloadFileResult {
    data class Saved(val file: File) : DownloadFileResult
    data object Rejected : DownloadFileResult
    data object Failed : DownloadFileResult
    data object Cancelled : DownloadFileResult
}

internal class SecureDownloadFileWriter(
    private val fetcher: SameOriginDownloadFetcher,
) {
    fun download(url: String, directory: File): DownloadFileResult {
        if (!directory.exists() && !directory.mkdirs()) return DownloadFileResult.Failed
        val temporary = try {
            File.createTempFile(PART_PREFIX, PART_SUFFIX, directory)
        } catch (_: IOException) {
            return DownloadFileResult.Failed
        }
        return try {
            val result = try {
                temporary.outputStream().buffered().use { output -> fetcher.fetch(url, output) }
            } catch (_: IOException) {
                if (fetcher.isCancelled()) DownloadFetchResult.Cancelled else DownloadFetchResult.Failed(
                    "Unable to write download",
                )
            }
            when (result) {
                is DownloadFetchResult.Success -> {
                    if (fetcher.isCancelled()) return DownloadFileResult.Cancelled
                    val destination = uniqueDestination(directory, result.filename)
                    try {
                        Files.move(temporary.toPath(), destination.toPath())
                        if (fetcher.isCancelled()) {
                            destination.delete()
                            DownloadFileResult.Cancelled
                        } else {
                            DownloadFileResult.Saved(destination)
                        }
                    } catch (_: IOException) {
                        DownloadFileResult.Failed
                    }
                }
                is DownloadFetchResult.Rejected -> DownloadFileResult.Rejected
                is DownloadFetchResult.Failed -> DownloadFileResult.Failed
                DownloadFetchResult.Cancelled -> DownloadFileResult.Cancelled
            }
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }

    private fun uniqueDestination(directory: File, filename: String): File {
        val safe = DownloadFilenamePolicy.sanitize(filename)
        var candidate = File(directory, safe)
        var counter = 1
        while (candidate.exists()) {
            candidate = File(directory, DownloadFilenamePolicy.sanitize("$counter-$safe"))
            counter += 1
        }
        return candidate
    }

    private companion object {
        const val PART_PREFIX = ".frame-download-"
        const val PART_SUFFIX = ".part"
    }
}
