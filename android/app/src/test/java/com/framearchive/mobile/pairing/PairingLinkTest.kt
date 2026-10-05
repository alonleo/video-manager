package com.framearchive.mobile.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

class PairingLinkTest {
    @Test
    fun `parses the planned pairing link example exactly`() {
        val link = PairingLink.parse(
            pairingUrl(server = "http://192.168.1.10:3000", token = "abc"),
        )

        assertEquals("http://192.168.1.10:3000", link.serverOrigin)
        assertEquals("abc", link.token)
    }

    @Test
    fun `normalizes HTTPS origins and ports`() {
        assertOrigin("https://Example.COM", "https://example.com")
        assertOrigin("https://Example.COM:443/", "https://example.com")
        assertOrigin("https://Example.COM:8443", "https://example.com:8443")
    }

    @Test
    fun `accepts only the allowed IPv4 ranges over HTTP`() {
        listOf(
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.0.1",
            "127.0.0.1",
            "127.255.255.254",
        ).forEach { host -> assertOrigin("http://$host", "http://$host") }

        listOf("172.15.255.255", "172.32.0.0").forEach(::assertServerRejected)
    }

    @Test
    fun `accepts localhost over HTTP case insensitively`() {
        assertOrigin("http://LOCALHOST:80/", "http://localhost")
        assertOrigin("http://localhost:8080", "http://localhost:8080")
    }

    @Test
    fun `accepts allowed IPv6 ranges over HTTP and emits brackets`() {
        assertOrigin("http://[fc00::1]", "http://[fc00::1]")
        assertOrigin("http://[fd12:3456::1]:8080/", "http://[fd12:3456::1]:8080")
        assertOrigin("http://[fe80::1]", "http://[fe80::1]")
        assertOrigin("http://[febf:ffff::1]", "http://[febf:ffff::1]")
        assertOrigin("http://[::1]", "http://[::1]")
    }

    @Test
    fun `canonicalizes expanded IPv6 loopback and removes the default port`() {
        assertOrigin("https://[0:0:0:0:0:0:0:1]:443/", "https://[::1]")
    }

    @Test
    fun `canonicalizes expanded ULA and link-local IPv6 with lowercase hex`() {
        assertOrigin(
            "http://[FD12:0000:0000:0000:0000:0000:0000:0001]:8080/",
            "http://[fd12::1]:8080",
        )
        assertOrigin(
            "http://[FE80:0000:0000:0000:0000:0000:ABCD:0001]/",
            "http://[fe80::abcd:1]",
        )
    }

    @Test
    fun `compresses the leftmost longest IPv6 zero run`() {
        assertOrigin(
            "https://[2001:0000:0000:0001:0000:0000:0001:0001]:8443",
            "https://[2001::1:0:0:1:1]:8443",
        )
    }

    @Test
    fun `does not compress a single IPv6 zero word`() {
        assertOrigin(
            "https://[2001:0DB8:0000:0001:0002:0003:0004:0005]",
            "https://[2001:db8:0:1:2:3:4:5]",
        )
    }

    @Test
    fun `rejects disallowed and IPv4-mapped IPv6 over HTTP`() {
        listOf(
            "http://[fec0::1]",
            "http://[::ffff:192.168.1.1]",
            "http://[::ffff:c0a8:101]",
        ).forEach(::assertServerRejected)
    }

    @Test
    fun `rejects public reserved documentation and link-local IPv4 over HTTP`() {
        listOf(
            "http://8.8.8.8",
            "http://169.254.1.1",
            "http://192.0.2.1",
            "http://198.51.100.1",
            "http://203.0.113.1",
            "http://198.18.0.1",
            "http://224.0.0.1",
            "http://240.0.0.1",
            "http://example.com",
            "http://192.168.example.com",
        ).forEach(::assertServerRejected)
    }

    @Test
    fun `rejects server values that are not strict origins`() {
        listOf(
            "https://user:password@example.com",
            "https://example.com/path",
            "https://example.com?query=1",
            "https://example.com#fragment",
            "ftp://example.com",
            "https:///",
            "https://example.com:0",
            "https://example.com:65536",
            "https://example.com:not-a-port",
        ).forEach(::assertServerRejected)
    }

    @Test
    fun `rejects malformed outer pairing protocol`() {
        val validServer = encode("https://example.com")
        listOf(
            "framearchive://pair?server=$validServer&token=abc",
            "framearchive://pair?v=1&server=$validServer",
            "framearchive://pair?v=1&token=abc",
            "framearchive://pair?v=1&v=1&server=$validServer&token=abc",
            "framearchive://pair?v=1&server=$validServer&server=$validServer&token=abc",
            "framearchive://pair?v=1&server=$validServer&token=abc&token=def",
            "framearchive://pair?v=1&server=$validServer&token=abc&extra=x",
            "framearchive://pair?v&server=$validServer&token=abc",
            "https://pair?v=1&server=$validServer&token=abc",
            "framearchive://other?v=1&server=$validServer&token=abc",
            "framearchive://pair/path?v=1&server=$validServer&token=abc",
            "framearchive://pair/?v=1&server=$validServer&token=abc",
            "framearchive://pair?v=1&server=$validServer&token=abc#fragment",
            "framearchive://pair:123?v=1&server=$validServer&token=abc",
            "framearchive://user@pair?v=1&server=$validServer&token=abc",
            "framearchive://pair?v=1&server=%ZZ&token=abc",
            "framearchive://pair?v=1&server=$validServer&token=%",
        ).forEach(::assertPairingRejected)
    }

    @Test
    fun `rejects unsupported versions with an upgrade message`() {
        val error = assertThrows(InvalidPairingLink::class.java) {
            PairingLink.parse(pairingUrl("https://example.com", "abc", version = "2"))
        }

        check(error.message.orEmpty().contains("升级")) {
            "未知版本错误应给出升级提示，实际消息为：${error.message}"
        }
    }

    @Test
    fun `rejects empty and whitespace-only tokens`() {
        listOf("", " ", "\t\n").forEach { token ->
            assertPairingRejected(pairingUrl("https://example.com", token))
        }
    }

    @Test
    fun `decodes token exactly once and preserves meaningful whitespace`() {
        val plus = PairingLink.parse(
            "framearchive://pair?v=1&server=https%3A%2F%2Fexample.com&token=a%2Bb",
        )
        assertEquals("a+b", plus.token)

        val encodedPercent = PairingLink.parse(
            "framearchive://pair?v=1&server=https%3A%2F%2Fexample.com&token=a%252Bb",
        )
        assertEquals("a%2Bb", encodedPercent.token)

        val padded = PairingLink.parse(pairingUrl("https://example.com", " abc "))
        assertEquals(" abc ", padded.token)

        val urlSafeBase64 = "Abc_123-Xyz"
        assertEquals(
            urlSafeBase64,
            PairingLink.parse(pairingUrl("https://example.com", urlSafeBase64)).token,
        )
    }

    private fun assertOrigin(rawOrigin: String, expectedOrigin: String) {
        assertEquals(
            expectedOrigin,
            PairingLink.parse(pairingUrl(rawOrigin, "abc")).serverOrigin,
        )
    }

    private fun assertServerRejected(rawOrigin: String) {
        val origin = if (rawOrigin.contains("://")) rawOrigin else "http://$rawOrigin"
        assertPairingRejected(pairingUrl(origin, "abc"))
    }

    private fun assertPairingRejected(raw: String) {
        assertThrows(InvalidPairingLink::class.java) { PairingLink.parse(raw) }
    }

    private fun pairingUrl(server: String, token: String, version: String = "1"): String =
        "framearchive://pair?v=${encode(version)}&server=${encode(server)}&token=${encode(token)}"

    private fun encode(value: String): String =
        URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20")
}
