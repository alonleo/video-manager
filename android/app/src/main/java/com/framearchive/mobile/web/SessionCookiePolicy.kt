package com.framearchive.mobile.web

internal data class SessionCookiePlan(
    val cookieUrl: String,
    val cookieValue: String,
) {
    override fun toString(): String =
        "SessionCookiePlan(cookieUrl=$cookieUrl, cookieValue=<redacted>)"
}

internal object SessionCookiePolicy {
    private const val COOKIE_NAME = "frame_device_session"

    fun plan(serverOrigin: String, setCookieHeader: String): SessionCookiePlan? {
        val policy = try {
            TrustedOriginPolicy(serverOrigin)
        } catch (_: IllegalArgumentException) {
            return null
        }
        val normalized = normalize(policy.normalizedOrigin, setCookieHeader) ?: return null
        return SessionCookiePlan("${policy.normalizedOrigin}/", normalized)
    }

    fun normalize(serverOrigin: String, setCookieHeader: String): String? {
        val policy = try {
            TrustedOriginPolicy(serverOrigin)
        } catch (_: IllegalArgumentException) {
            return null
        }
        if (setCookieHeader.any { (it.code < 0x20 && it != '\t') || it.code == 0x7f }) return null
        if (
            setCookieHeader.split(';').drop(1).any {
                it.trimStart().startsWith("$COOKIE_NAME=", ignoreCase = true)
            }
        ) return null
        val pair = setCookieHeader.substringBefore(';')
        val equals = pair.indexOf('=')
        if (equals <= 0 || pair.substring(0, equals) != COOKIE_NAME) return null
        val value = pair.substring(equals + 1)
        if (value.isEmpty() || !value.all(::isCookieOctet)) return null
        val secure = if (policy.normalizedOrigin.startsWith("https://")) "; Secure" else ""
        return "$COOKIE_NAME=$value; Path=/; HttpOnly; SameSite=Lax$secure"
    }

    private fun isCookieOctet(character: Char): Boolean = when (character.code) {
        0x21, in 0x23..0x2b, in 0x2d..0x3a, in 0x3c..0x5b, in 0x5d..0x7e -> true
        else -> false
    }
}
