package com.framearchive.mobile.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import com.framearchive.mobile.network.AuthSession
import com.framearchive.mobile.network.DeviceRevoked
import com.framearchive.mobile.network.NetworkUnavailable
import com.framearchive.mobile.network.PairingExpired
import com.framearchive.mobile.network.PairingRejected
import com.framearchive.mobile.pairing.PairingCoordinator
import com.framearchive.mobile.pairing.PairingLink
import com.framearchive.mobile.storage.DeviceIdentity
import com.framearchive.mobile.storage.PendingPairing
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelTest {
    private val dispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun restoresPendingPairingAfterRestart() = runTest(dispatcher) {
        val identity = pendingIdentity()
        val fake = FakeCoordinator(identity).apply { approvalResult = CompletableDeferred() }

        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        assertEquals(AppState.AwaitingApproval("123456", EXPIRES_AT), viewModel.state.value)
        assertEquals(1, fake.waitCalls)
        assertEquals("192.168.1.70", viewModel.approvalDetails.value?.serverHost)
        assertEquals("测试手机", viewModel.approvalDetails.value?.deviceName)
    }

    @Test
    fun approvedPairingTransitionsToAuthenticatingThenBrowsing() = runTest(dispatcher) {
        val pending = pendingIdentity()
        val approved = pending.copy(pending = null)
        val authentication = CompletableDeferred<AuthSession>()
        val fake = FakeCoordinator(pending).apply {
            approvalResult = CompletableDeferred(approved)
            authenticationResult = authentication
        }
        val viewModel = MainViewModel(fake, dispatcher) { NOW }

        runCurrent()
        assertEquals(AppState.Authenticating, viewModel.state.value)
        authentication.complete(AuthSession("frame_device_session=secret; HttpOnly"))
        runCurrent()

        assertEquals(
            AppState.Browsing(approved.serverOrigin, "frame_device_session=secret; HttpOnly"),
            viewModel.state.value,
        )
        assertEquals(1, fake.authenticateCalls)
        assertFalse(viewModel.state.value.toString().contains("secret"))
    }

    @Test
    fun rejectedPairingClearsIdentityAndReturnsToScanner() = runTest(dispatcher) {
        val fake = FakeCoordinator(pendingIdentity()).apply {
            approvalResult = CompletableDeferred<DeviceIdentity>().also {
                it.completeExceptionally(PairingRejected())
            }
            clearIdentityOnWaitFailure = true
        }

        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        assertEquals(
            AppState.RecoverableError("配对申请已被电脑拒绝，请重新扫码", false),
            viewModel.state.value,
        )
        assertNull(fake.identity)
        assertFalse(fake.cancelled)
    }

    @Test
    fun expiredPairingClearsIdentityAndReturnsToScanner() = runTest(dispatcher) {
        val fake = FakeCoordinator(pendingIdentity()).apply {
            approvalResult = CompletableDeferred<DeviceIdentity>().also {
                it.completeExceptionally(PairingExpired())
            }
            clearIdentityOnWaitFailure = true
        }

        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        assertEquals(AppState.NeedsPairing, viewModel.state.value)
        assertNull(fake.identity)
        assertFalse(fake.cancelled)
    }

    @Test
    fun networkFailureKeepsRetryablePendingIdentity() = runTest(dispatcher) {
        val pending = pendingIdentity()
        val fake = FakeCoordinator(pending).apply {
            approvalResult = CompletableDeferred<DeviceIdentity>().also {
                it.completeExceptionally(NetworkUnavailable(IOException("offline")))
            }
        }

        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        assertEquals(AppState.RecoverableError("网络连接失败，请重试", true), viewModel.state.value)
        assertEquals(pending, fake.identity)

        fake.approvalResult = CompletableDeferred()
        viewModel.retry()
        runCurrent()
        assertEquals(AppState.AwaitingApproval("123456", EXPIRES_AT), viewModel.state.value)
        assertEquals(2, fake.waitCalls)
    }

    @Test
    fun revokedIdentityReturnsToScanner() = runTest(dispatcher) {
        val approved = pendingIdentity().copy(pending = null)
        val fake = FakeCoordinator(approved).apply {
            authenticationResult = CompletableDeferred<AuthSession>().also {
                it.completeExceptionally(DeviceRevoked())
            }
            clearIdentityOnAuthFailure = true
        }

        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        assertEquals(AppState.NeedsPairing, viewModel.state.value)
        assertNull(fake.identity)
        assertFalse(fake.cancelled)
    }

    @Test
    fun submissionParsesLinkBeforeCallingRepositoryAndCanBeCancelled() = runTest(dispatcher) {
        val submitted = pendingIdentity()
        val fake = FakeCoordinator(null).apply {
            submitResult = submitted
            approvalResult = CompletableDeferred()
        }
        val viewModel = MainViewModel(fake, dispatcher) { NOW }

        runCurrent()
        viewModel.submit(
            "framearchive://pair?v=1&server=http%3A%2F%2F192.168.1.70%3A8420&token=${"a".repeat(43)}",
            "测试手机",
        )
        runCurrent()

        assertEquals(1, fake.submitCalls)
        assertEquals(AppState.AwaitingApproval("123456", EXPIRES_AT), viewModel.state.value)
        viewModel.cancelPairing()
        runCurrent()
        assertTrue(fake.cancelled)
        assertEquals(AppState.NeedsPairing, viewModel.state.value)
    }

    @Test
    fun invalidSubmissionNeverCallsRepository() = runTest(dispatcher) {
        val fake = FakeCoordinator(null)
        val viewModel = MainViewModel(fake, dispatcher) { NOW }
        runCurrent()

        viewModel.submit("https://192.168.1.70:8420/pair", "测试手机")
        runCurrent()

        assertEquals(0, fake.submitCalls)
        assertEquals(
            AppState.RecoverableError("配对链接无效，请检查后重试", false),
            viewModel.state.value,
        )
    }

    @Test
    fun clearingViewModelCancelsPollingWithoutClearingStoredIdentity() = runTest(dispatcher) {
        val pending = pendingIdentity()
        val fake = FakeCoordinator(pending).apply { approvalResult = CompletableDeferred() }
        val store = ViewModelStore()
        ViewModelProvider(
            store,
            object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T =
                    MainViewModel(fake, dispatcher) { NOW } as T
            },
        )[MainViewModel::class.java]
        runCurrent()

        store.clear()
        runCurrent()

        assertTrue(fake.waitCancelled)
        assertEquals(pending, fake.identity)
        assertFalse(fake.cancelled)
    }

    private class FakeCoordinator(initialIdentity: DeviceIdentity?) : PairingCoordinator {
        var identity = initialIdentity
        var submitResult: DeviceIdentity? = null
        var approvalResult = CompletableDeferred<DeviceIdentity>()
        var authenticationResult = CompletableDeferred<AuthSession>()
        var waitCalls = 0
        var authenticateCalls = 0
        var submitCalls = 0
        var cancelled = false
        var waitCancelled = false
        var clearIdentityOnWaitFailure = false
        var clearIdentityOnAuthFailure = false

        override fun storedIdentity(): DeviceIdentity? = identity

        override suspend fun submit(link: PairingLink, name: String): DeviceIdentity {
            submitCalls += 1
            return requireNotNull(submitResult).also { identity = it }
        }

        override suspend fun waitForApproval(identity: DeviceIdentity): DeviceIdentity {
            waitCalls += 1
            return try {
                approvalResult.await().also { this.identity = it }
            } catch (error: Throwable) {
                if (clearIdentityOnWaitFailure) this.identity = null
                if (error is CancellationException) waitCancelled = true
                throw error
            }
        }

        override suspend fun awaitApproval(identity: DeviceIdentity): AuthSession =
            authenticate(waitForApproval(identity))

        override suspend fun authenticate(identity: DeviceIdentity): AuthSession {
            authenticateCalls += 1
            return try {
                authenticationResult.await()
            } catch (error: Throwable) {
                if (clearIdentityOnAuthFailure) this.identity = null
                throw error
            }
        }

        override suspend fun resume(identity: DeviceIdentity): AuthSession =
            if (identity.pending != null) awaitApproval(identity) else authenticate(identity)

        override fun cancel() {
            cancelled = true
            identity = null
        }
    }

    private companion object {
        const val NOW = 1_000L
        const val EXPIRES_AT = 121_000L

        fun pendingIdentity() = DeviceIdentity(
            serverOrigin = "http://192.168.1.70:8420",
            deviceId = 7,
            deviceName = "测试手机",
            keyAlias = "alias",
            pending = PendingPairing(
                pairingId = 8,
                claimSecret = "claim",
                verificationCode = "123456",
                expiresAtEpochMillis = EXPIRES_AT,
            ),
        )
    }
}
