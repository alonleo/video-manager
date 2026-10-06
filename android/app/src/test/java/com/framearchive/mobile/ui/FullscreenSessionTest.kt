package com.framearchive.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class FullscreenSessionTest {
    @Test
    fun acceptsOneFullscreenViewAndRejectsASecondRequest() {
        val hidden = mutableListOf<String>()
        val session = FullscreenSession<String, String> { hidden += it }

        assertTrue(session.show("first-view", "first-callback"))
        assertFalse(session.show("second-view", "second-callback"))

        assertSame("first-view", session.currentView)
        assertEquals(listOf("second-callback"), hidden)
    }

    @Test
    fun hideClearsTheSessionAndNotifiesTheAcceptedCallback() {
        val hidden = mutableListOf<String>()
        val session = FullscreenSession<String, String> { hidden += it }
        session.show("view", "callback")

        assertEquals("view", session.hide())

        assertNull(session.currentView)
        assertEquals(listOf("callback"), hidden)
        assertNull(session.hide())
        assertEquals(listOf("callback"), hidden)
    }
}
