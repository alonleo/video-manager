package com.framearchive.mobile.pairing

import java.io.ByteArrayOutputStream
import java.net.URI
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.Locale

data class PairingLink(val serverOrigin: String, val token: String) {
    companion object {
        fun parse(raw: String): PairingLink {
            try {
                val uri = URI(raw)
                requirePairingEnvelope(uri)
                val parameters = parseQuery(uri.rawQuery)

                if (parameters.getValue("v") != "1") {
                    throw InvalidPairingLink("二维码版本不受支持，请升级应用后重试")
                }

                val token = parameters.getValue("token")
                if (token.isBlank()) {
                    throw InvalidPairingLink("配对 token 不能为空")
                }

                return PairingLink(
                    serverOrigin = TrustedServerOrigin.parse(parameters.getValue("server")),
                    token = token,
                )
            } catch (error: InvalidPairingLink) {
                throw error
            } catch (_: Exception) {
                throw InvalidPairingLink("配对二维码格式无效")
            }
        }

        private fun requirePairingEnvelope(uri: URI) {
            if (
                !uri.scheme.equals("framearchive", ignoreCase = true) ||
                !uri.host.equals("pair", ignoreCase = true) ||
                uri.rawAuthority == null ||
                uri.rawUserInfo != null ||
                uri.port != -1 ||
                !uri.rawPath.isNullOrEmpty() ||
                uri.rawFragment != null ||
                uri.rawQuery == null
            ) {
                throw InvalidPairingLink("不是有效的配对二维码")
            }
        }

        private fun parseQuery(rawQuery: String): Map<String, String> {
            val values = linkedMapOf<String, String>()
            for (part in rawQuery.split('&')) {
                val equals = part.indexOf('=')
                if (equals <= 0) {
                    throw InvalidPairingLink("配对二维码参数格式无效")
                }
                val name = part.substring(0, equals)
                if (name !in REQUIRED_PARAMETERS || values.containsKey(name)) {
                    throw InvalidPairingLink("配对二维码包含未知或重复参数")
                }
                values[name] = percentDecode(part.substring(equals + 1))
            }
            if (values.keys != REQUIRED_PARAMETERS) {
                throw InvalidPairingLink("配对二维码缺少必要参数")
            }
            return values
        }

        private fun percentDecode(raw: String): String {
            val bytes = ByteArrayOutputStream(raw.length)
            var index = 0
            while (index < raw.length) {
                if (raw[index] == '%') {
                    if (index + 2 >= raw.length) {
                        throw InvalidPairingLink("配对二维码包含非法 percent 编码")
                    }
                    val high = raw[index + 1].digitToIntOrNull(16)
                    val low = raw[index + 2].digitToIntOrNull(16)
                    if (high == null || low == null) {
                        throw InvalidPairingLink("配对二维码包含非法 percent 编码")
                    }
                    bytes.write(high * 16 + low)
                    index += 3
                } else {
                    val codePoint = raw.codePointAt(index)
                    bytes.write(String(Character.toChars(codePoint)).toByteArray(StandardCharsets.UTF_8))
                    index += Character.charCount(codePoint)
                }
            }

            return StandardCharsets.UTF_8
                .newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes.toByteArray()))
                .toString()
        }

        private val REQUIRED_PARAMETERS = setOf("v", "server", "token")
    }
}

class InvalidPairingLink(message: String) : IllegalArgumentException(message)

internal object TrustedServerOrigin {
    fun parse(raw: String): String {
        val uri = try {
            URI(raw)
        } catch (_: Exception) {
            throw InvalidPairingLink("服务器地址格式无效")
        }

        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        if (scheme != "http" && scheme != "https") {
            throw InvalidPairingLink("服务器地址只允许 http 或 https")
        }
        if (
            uri.isOpaque ||
            uri.rawAuthority == null ||
            uri.rawUserInfo != null ||
            uri.host == null ||
            (uri.rawPath != null && uri.rawPath != "" && uri.rawPath != "/") ||
            uri.rawQuery != null ||
            uri.rawFragment != null
        ) {
            throw InvalidPairingLink("服务器地址必须是严格的 origin")
        }

        val port = try {
            uri.port
        } catch (_: Exception) {
            throw InvalidPairingLink("服务器端口无效")
        }
        if (hasExplicitInvalidPort(uri.rawAuthority, port) || port == 0 || port > 65535) {
            throw InvalidPairingLink("服务器端口无效")
        }

        val rawHost = uri.host.lowercase(Locale.ROOT)
        val isIpv6 = rawHost.startsWith('[') && rawHost.endsWith(']')
        val host = if (isIpv6) rawHost.substring(1, rawHost.length - 1) else rawHost
        validateHost(host, isIpv6)

        if (scheme == "http" && !isTrustedHttpHost(host, isIpv6)) {
            throw InvalidPairingLink("HTTP 服务器必须位于允许的本地网络")
        }

        val normalizedHost = if (isIpv6) "[$host]" else host
        val normalizedPort = when {
            port == -1 -> ""
            scheme == "http" && port == 80 -> ""
            scheme == "https" && port == 443 -> ""
            else -> ":$port"
        }
        return "$scheme://$normalizedHost$normalizedPort"
    }

    private fun hasExplicitInvalidPort(authority: String, port: Int): Boolean {
        if (port != -1) return false
        return if (authority.startsWith('[')) {
            val closingBracket = authority.indexOf(']')
            closingBracket < 0 || authority.length > closingBracket + 1
        } else {
            authority.contains(':')
        }
    }

    private fun validateHost(host: String, isIpv6: Boolean) {
        if (isIpv6) {
            if (parseIpv6(host) == null) throw InvalidPairingLink("IPv6 地址无效")
            return
        }

        val ipv4 = parseIpv4(host)
        if (ipv4 != null) return
        if (host.all { it.isDigit() || it == '.' }) {
            throw InvalidPairingLink("IPv4 地址无效")
        }
        if (
            host.length > 253 ||
            host.endsWith('.') ||
            host.split('.').any { label ->
                label.isEmpty() || label.length > 63 ||
                    label.first() == '-' || label.last() == '-' ||
                    label.any { !it.isLetterOrDigit() && it != '-' }
            }
        ) {
            throw InvalidPairingLink("服务器主机名无效")
        }
    }

    private fun isTrustedHttpHost(host: String, isIpv6: Boolean): Boolean {
        if (!isIpv6 && host == "localhost") return true
        if (!isIpv6) {
            val octets = parseIpv4(host) ?: return false
            return octets[0] == 10 ||
                octets[0] == 127 ||
                (octets[0] == 172 && octets[1] in 16..31) ||
                (octets[0] == 192 && octets[1] == 168)
        }

        val bytes = parseIpv6(host) ?: return false
        if (bytes.take(10).all { it == 0 } && bytes[10] == 0xff && bytes[11] == 0xff) {
            return false
        }
        val first = bytes[0]
        val second = bytes[1]
        val isUniqueLocal = first in 0xfc..0xfd
        val isLinkLocal = first == 0xfe && second in 0x80..0xbf
        val isLoopback = bytes.take(15).all { it == 0 } && bytes[15] == 1
        return isUniqueLocal || isLinkLocal || isLoopback
    }

    private fun parseIpv4(host: String): IntArray? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        return IntArray(4).also { octets ->
            for ((index, part) in parts.withIndex()) {
                if (part.isEmpty() || part.any { !it.isDigit() }) return null
                if (part.length > 1 && part.startsWith('0')) return null
                octets[index] = part.toIntOrNull()?.takeIf { it in 0..255 } ?: return null
            }
        }
    }

    private fun parseIpv6(host: String): IntArray? {
        if (host.isEmpty() || '%' in host || host.count { it == ':' } < 2) return null
        if (host.indexOf("::") != host.lastIndexOf("::")) return null

        val compressed = "::" in host
        val halves = if (compressed) host.split("::", limit = 2) else listOf(host)
        val left = parseIpv6Words(halves[0], allowIpv4 = !compressed || halves[1].isEmpty()) ?: return null
        val right = if (compressed) parseIpv6Words(halves[1], allowIpv4 = true) ?: return null else emptyList()
        val wordCount = left.size + right.size
        if ((!compressed && wordCount != 8) || (compressed && wordCount >= 8)) return null

        val words = left + List(8 - wordCount) { 0 } + right
        return IntArray(16).also { bytes ->
            words.forEachIndexed { index, word ->
                bytes[index * 2] = word ushr 8
                bytes[index * 2 + 1] = word and 0xff
            }
        }
    }

    private fun parseIpv6Words(part: String, allowIpv4: Boolean): List<Int>? {
        if (part.isEmpty()) return emptyList()
        val pieces = part.split(':')
        if (pieces.any { it.isEmpty() }) return null
        val words = mutableListOf<Int>()
        for ((index, piece) in pieces.withIndex()) {
            if ('.' in piece) {
                if (!allowIpv4 || index != pieces.lastIndex) return null
                val octets = parseIpv4(piece) ?: return null
                words += octets[0] * 256 + octets[1]
                words += octets[2] * 256 + octets[3]
            } else {
                if (piece.length !in 1..4 || piece.any { it.digitToIntOrNull(16) == null }) return null
                words += piece.toInt(16)
            }
        }
        return words
    }
}
