package com.framearchive.mobile.web

import java.net.IDN
import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI

enum class NavigationDecision {
    SAME_ORIGIN,
    EXTERNAL_HTTPS,
    BLOCK,
}

class TrustedOriginPolicy(serverOrigin: String) {
    private val trustedOrigin = parseWebUrl(serverOrigin, requireOriginOnly = true)
        ?: throw IllegalArgumentException("Trusted server must be a valid HTTP(S) origin")

    val normalizedOrigin: String = trustedOrigin.serialize()

    fun decide(candidate: String): NavigationDecision {
        val parsed = parseWebUrl(candidate, requireOriginOnly = false) ?: return NavigationDecision.BLOCK
        return when {
            parsed == trustedOrigin -> NavigationDecision.SAME_ORIGIN
            parsed.scheme == HTTPS -> NavigationDecision.EXTERNAL_HTTPS
            else -> NavigationDecision.BLOCK
        }
    }

    private data class WebOrigin(val scheme: String, val host: String, val port: Int) {
        fun serialize(): String {
            val renderedHost = if (host.contains(':')) "[$host]" else host
            val defaultPort = if (scheme == HTTPS) HTTPS_PORT else HTTP_PORT
            return if (port == defaultPort) "$scheme://$renderedHost" else "$scheme://$renderedHost:$port"
        }
    }

    private companion object {
        const val HTTP = "http"
        const val HTTPS = "https"
        const val HTTP_PORT = 80
        const val HTTPS_PORT = 443

        fun parseWebUrl(raw: String, requireOriginOnly: Boolean): WebOrigin? {
            if (raw.isBlank() || raw.any { it.code <= 0x20 || it.code == 0x7f }) return null
            val uri = try {
                URI(raw)
            } catch (_: Exception) {
                return null
            }
            val scheme = uri.scheme?.lowercase() ?: return null
            if (scheme != HTTP && scheme != HTTPS) return null
            if (uri.isOpaque || uri.rawAuthority.isNullOrEmpty() || uri.rawUserInfo != null) return null
            val rawHost = uri.host ?: return null
            val host = normalizeHost(rawHost) ?: return null
            val port = when {
                uri.port == -1 && scheme == HTTP -> HTTP_PORT
                uri.port == -1 -> HTTPS_PORT
                uri.port in 1..65535 -> uri.port
                else -> return null
            }
            if (requireOriginOnly) {
                if (uri.rawPath.orEmpty() !in setOf("", "/")) return null
                if (uri.rawQuery != null || uri.rawFragment != null) return null
            }
            return WebOrigin(scheme, host, port)
        }

        fun normalizeHost(rawHost: String): String? {
            val unwrapped = rawHost.removePrefix("[").removeSuffix("]")
            if (unwrapped.isEmpty() || unwrapped.contains('%')) return null
            return if (unwrapped.contains(':')) {
                try {
                    (InetAddress.getByName(unwrapped) as? Inet6Address)?.hostAddress?.lowercase()
                } catch (_: Exception) {
                    null
                }
            } else {
                try {
                    IDN.toASCII(unwrapped, IDN.USE_STD3_ASCII_RULES)
                        .lowercase()
                        .takeIf { it.isNotEmpty() }
                } catch (_: IllegalArgumentException) {
                    null
                }
            }
        }
    }
}
