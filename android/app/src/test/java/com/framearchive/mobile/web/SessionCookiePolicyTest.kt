package com.framearchive.mobile.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class SessionCookiePolicyTest {
    @Test
    fun normalizesOnlyTheExpectedSessionCookie() {
        assertEquals(
            "frame_device_session=abc-_.~123; Path=/; HttpOnly; SameSite=Lax",
            SessionCookiePolicy.normalize(
                "http://192.168.1.70:8420",
                "frame_device_session=abc-_.~123; Path=/admin; Domain=evil.example; HttpOnly",
            ),
        )
        assertEquals(
            "frame_device_session=abc123; Path=/; HttpOnly; SameSite=Lax; Secure",
            SessionCookiePolicy.normalize(
                "https://video.example.com",
                "frame_device_session=abc123; Path=/; Secure",
            ),
        )
    }

    @Test
    fun rejectsHeaderAndCookieInjection() {
        listOf(
            "other=value",
            "frame_device_session=",
            "frame_device_session=one; frame_device_session=two",
            "frame_device_session=bad value",
            "frame_device_session=bad,value",
            "frame_device_session=bad\\value",
            "frame_device_session=value\r\nSet-Cookie: other=stolen",
            "frame_device_session=value\u0000",
        ).forEach { header ->
            assertNull(header, SessionCookiePolicy.normalize("https://video.example.com", header))
        }
    }

    @Test
    fun cookiePlanDoesNotExposeTheSecret() {
        val plan = SessionCookiePolicy.plan(
            "https://video.example.com",
            "frame_device_session=top-secret; Path=/",
        )!!

        assertEquals("https://video.example.com/", plan.cookieUrl)
        assertFalse(plan.toString().contains("top-secret"))
        assertFalse(plan.toString().contains(plan.cookieValue))
    }
}
