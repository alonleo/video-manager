package com.framearchive.mobile.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebDataOperationCoordinatorTest {
    @Test
    fun cleanupInvalidatesAnAlreadyOpenSessionBeforeAReplacementCanRun() {
        val coordinator = WebDataOperationCoordinator()
        val old = coordinator.open { }
        lateinit var finish: () -> Unit

        coordinator.clear { finish = it }
        val replacement = coordinator.open { }

        assertFalse(old.isCurrent())
        assertFalse(replacement.isCurrent())
        finish()
        assertFalse(old.isCurrent())
        assertTrue(replacement.isCurrent())
    }

    @Test
    fun openWaitsUntilAsynchronousCleanupCompletes() {
        val coordinator = WebDataOperationCoordinator()
        val events = mutableListOf<String>()
        var completeCleanup: (() -> Unit)? = null

        coordinator.clear { complete ->
            events += "clear-start"
            completeCleanup = complete
        }
        val token = coordinator.open { events += "open" }

        assertEquals(listOf("clear-start"), events)
        assertFalse(token.isCurrent())
        completeCleanup!!()
        assertEquals(listOf("clear-start", "open"), events)
        assertTrue(token.isCurrent())
    }

    @Test
    fun latestQueuedOpenReplacesOlderOneAndStaleCallbacksStayInvalid() {
        val coordinator = WebDataOperationCoordinator()
        val events = mutableListOf<String>()
        lateinit var finish: () -> Unit
        coordinator.clear { finish = it }
        val first = coordinator.open { events += "old" }
        val second = coordinator.open { events += "new" }

        finish()

        assertEquals(listOf("new"), events)
        assertFalse(first.isCurrent())
        assertTrue(second.isCurrent())
    }
}
