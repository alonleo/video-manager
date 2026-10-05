package com.framearchive.mobile.web

internal class WebDataOperationCoordinator {
    private var generation = 0L
    private var activeGeneration = -1L
    private var cleanupInFlight = false
    private var pendingOpen: PendingOpen? = null

    @Synchronized
    fun clear(start: ((() -> Unit) -> Unit)) {
        generation += 1
        activeGeneration = -1L
        pendingOpen = null
        if (cleanupInFlight) return
        cleanupInFlight = true
        var completed = false
        start {
            val open = synchronized(this) {
                if (completed) return@start
                completed = true
                cleanupInFlight = false
                pendingOpen.also { pendingOpen = null }
            }
            open?.let(::activate)
        }
    }

    @Synchronized
    fun open(action: (WebDataOperationToken) -> Unit): WebDataOperationToken {
        val id = ++generation
        val pending = PendingOpen(id, action)
        if (cleanupInFlight) {
            pendingOpen = pending
        } else {
            activate(pending)
        }
        return WebDataOperationToken(this, id)
    }

    @Synchronized
    fun invalidate() {
        generation += 1
        activeGeneration = -1L
        pendingOpen = null
    }

    @Synchronized
    internal fun isCurrent(id: Long): Boolean = activeGeneration == id

    @Synchronized
    internal fun cancel(id: Long) {
        if (activeGeneration == id) activeGeneration = -1L
        if (pendingOpen?.id == id) pendingOpen = null
    }

    @Synchronized
    private fun activate(open: PendingOpen) {
        activeGeneration = open.id
        open.action(WebDataOperationToken(this, open.id))
    }

    private data class PendingOpen(
        val id: Long,
        val action: (WebDataOperationToken) -> Unit,
    )
}

internal class WebDataOperationToken(
    private val coordinator: WebDataOperationCoordinator,
    private val id: Long,
) {
    fun isCurrent(): Boolean = coordinator.isCurrent(id)
    fun cancel() = coordinator.cancel(id)
}
