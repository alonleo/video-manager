package com.framearchive.mobile.ui

sealed interface AppState {
    data object NeedsPairing : AppState
    data object Submitting : AppState
    data class AwaitingApproval(val code: String, val expiresAt: Long) : AppState
    data object Authenticating : AppState
    data class Browsing(val serverOrigin: String, val cookieHeader: String) : AppState {
        override fun toString(): String =
            "Browsing(serverOrigin=$serverOrigin, cookieHeader=<redacted>)"
    }
    data class RecoverableError(val message: String, val canRetry: Boolean) : AppState
}
