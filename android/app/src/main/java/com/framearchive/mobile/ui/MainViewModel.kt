package com.framearchive.mobile.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.framearchive.mobile.network.AuthSession
import com.framearchive.mobile.network.DeviceRevoked
import com.framearchive.mobile.network.NetworkUnavailable
import com.framearchive.mobile.network.PairingExpired
import com.framearchive.mobile.network.PairingRejected
import com.framearchive.mobile.pairing.PairingCoordinator
import com.framearchive.mobile.pairing.PairingLink
import com.framearchive.mobile.storage.DeviceIdentity
import com.framearchive.mobile.storage.IdentityStoreCorrupted
import com.framearchive.mobile.storage.IdentityStoreUnavailable
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.yield
import java.net.URI

data class ApprovalDetails(val serverHost: String, val deviceName: String)
data class PairingDraft(val pairingLink: String, val deviceName: String) {
    override fun toString(): String =
        "PairingDraft(pairingLink=<redacted>, deviceName=$deviceName)"
}

class MainViewModel(
    private val coordinator: PairingCoordinator,
    private val dispatcher: CoroutineDispatcher = Dispatchers.IO,
    initialDeviceName: String = "",
    private val nowEpochMillis: () -> Long = System::currentTimeMillis,
) : ViewModel() {
    private val mutableState = MutableStateFlow<AppState>(AppState.NeedsPairing)
    val state: StateFlow<AppState> = mutableState.asStateFlow()

    private val mutableApprovalDetails = MutableStateFlow<ApprovalDetails?>(null)
    val approvalDetails: StateFlow<ApprovalDetails?> = mutableApprovalDetails.asStateFlow()

    private val mutablePairingDraft = MutableStateFlow(PairingDraft("", initialDeviceName))
    val pairingDraft: StateFlow<PairingDraft> = mutablePairingDraft.asStateFlow()

    private val mutableCanResetIdentity = MutableStateFlow(false)
    val canResetIdentity: StateFlow<Boolean> = mutableCanResetIdentity.asStateFlow()

    private var operation: Job? = null

    init {
        recover()
    }

    fun updatePairingLink(value: String) {
        mutablePairingDraft.value = mutablePairingDraft.value.copy(pairingLink = value)
    }

    fun updateDeviceName(value: String) {
        mutablePairingDraft.value = mutablePairingDraft.value.copy(deviceName = value)
    }

    fun updatePairingLinkFromScan(contents: String?): Boolean {
        val scanned = contents?.trim().orEmpty()
        if (scanned.isEmpty()) return false
        updatePairingLink(scanned)
        return true
    }

    fun submit() {
        val draft = mutablePairingDraft.value
        submitDraft(draft.pairingLink, draft.deviceName)
    }

    fun submit(rawLink: String, deviceName: String) {
        mutablePairingDraft.value = PairingDraft(rawLink, deviceName)
        submitDraft(rawLink, deviceName)
    }

    private fun submitDraft(rawLink: String, deviceName: String) {
        launchOperation {
            transitionTo(AppState.Submitting)
            val link = try {
                PairingLink.parse(rawLink)
            } catch (error: IllegalArgumentException) {
                transitionTo(AppState.RecoverableError("配对链接无效，请检查后重试", false))
                return@launchOperation
            }
            if (deviceName.isBlank()) {
                transitionTo(AppState.RecoverableError("请输入设备名称", false))
                return@launchOperation
            }
            try {
                val identity = coordinator.submit(link, deviceName.trim())
                updatePairingLink("")
                awaitAndAuthenticate(identity)
            } catch (error: Throwable) {
                handleFailure(error)
            }
        }
    }

    fun retry() {
        recover()
    }

    fun cancelPairing() {
        operation?.cancel()
        operation = null
        try {
            coordinator.cancel()
        } catch (_: Throwable) {
            transitionTo(AppState.RecoverableError("无法取消配对，请重试", true))
            return
        }
        mutableApprovalDetails.value = null
        transitionTo(AppState.NeedsPairing)
    }

    fun abandonIdentity() {
        if (!mutableCanResetIdentity.value) return
        cancelPairing()
    }

    fun remainingApprovalMillis(): Long =
        ((mutableState.value as? AppState.AwaitingApproval)?.expiresAt ?: nowEpochMillis()) - nowEpochMillis()

    private fun recover() {
        launchOperation {
            val identity = try {
                coordinator.storedIdentity()
            } catch (_: IdentityStoreUnavailable) {
                showIdentityStoreUnavailable()
                return@launchOperation
            } catch (_: IdentityStoreCorrupted) {
                showIdentityStoreCorrupted()
                return@launchOperation
            } catch (error: Throwable) {
                transitionTo(AppState.RecoverableError("无法读取设备身份，请重新配对", false))
                return@launchOperation
            }
            when {
                identity == null -> {
                    mutableApprovalDetails.value = null
                    transitionTo(AppState.NeedsPairing)
                }
                identity.pending != null -> try {
                    awaitAndAuthenticate(identity)
                } catch (error: Throwable) {
                    handleFailure(error)
                }
                else -> try {
                    authenticate(identity)
                } catch (error: Throwable) {
                    handleFailure(error)
                }
            }
        }
    }

    private suspend fun awaitAndAuthenticate(identity: DeviceIdentity) {
        showAwaiting(identity)
        yield()
        val approved = coordinator.waitForApproval(identity)
        authenticate(approved)
    }

    private suspend fun authenticate(identity: DeviceIdentity) {
        mutableApprovalDetails.value = null
        transitionTo(AppState.Authenticating)
        yield()
        val session = coordinator.authenticate(identity)
        showBrowsing(identity, session)
    }

    private fun showAwaiting(identity: DeviceIdentity) {
        val pending = requireNotNull(identity.pending)
        mutableApprovalDetails.value = ApprovalDetails(
            serverHost = URI(identity.serverOrigin).host ?: identity.serverOrigin,
            deviceName = identity.deviceName,
        )
        transitionTo(
            AppState.AwaitingApproval(
                code = pending.verificationCode,
                expiresAt = pending.expiresAtEpochMillis,
            ),
        )
    }

    private fun showBrowsing(identity: DeviceIdentity, session: AuthSession) {
        transitionTo(AppState.Browsing(identity.serverOrigin, session.setCookieHeader))
    }

    private fun handleFailure(error: Throwable) {
        if (error is CancellationException) throw error
        when (error) {
            is IdentityStoreUnavailable -> showIdentityStoreUnavailable()
            is IdentityStoreCorrupted -> showIdentityStoreCorrupted()
            is PairingRejected -> {
                mutableApprovalDetails.value = null
                transitionTo(
                    AppState.RecoverableError(
                        "配对申请已被电脑拒绝，请重新扫码",
                        false,
                    ),
                )
            }
            is PairingExpired -> {
                mutableApprovalDetails.value = null
                transitionTo(
                    AppState.RecoverableError(
                        "二维码已过期，请重新扫码",
                        false,
                    ),
                )
            }
            is DeviceRevoked -> {
                mutableApprovalDetails.value = null
                transitionTo(AppState.NeedsPairing)
            }
            is NetworkUnavailable -> {
                val identityStillAvailable = try {
                    coordinator.storedIdentity() != null
                } catch (_: IdentityStoreUnavailable) {
                    showIdentityStoreUnavailable()
                    return
                } catch (_: IdentityStoreCorrupted) {
                    showIdentityStoreCorrupted()
                    return
                }
                transitionTo(
                    AppState.RecoverableError("网络连接失败，请重试", true),
                    canResetIdentity = identityStillAvailable,
                )
            }
            else -> {
                val identityStillAvailable = try {
                    coordinator.storedIdentity() != null
                } catch (_: IdentityStoreUnavailable) {
                    showIdentityStoreUnavailable()
                    return
                } catch (_: IdentityStoreCorrupted) {
                    showIdentityStoreCorrupted()
                    return
                }
                if (identityStillAvailable) {
                    transitionTo(
                        AppState.RecoverableError("操作失败，请重试", true),
                        canResetIdentity = true,
                    )
                } else {
                    mutableApprovalDetails.value = null
                    transitionTo(AppState.NeedsPairing)
                }
            }
        }
    }

    private fun showIdentityStoreUnavailable() {
        transitionTo(AppState.RecoverableError("设备身份暂时无法读取，请重试", true))
    }

    private fun showIdentityStoreCorrupted() {
        transitionTo(AppState.RecoverableError("设备身份已损坏，请重新配对", false))
    }

    private fun transitionTo(state: AppState, canResetIdentity: Boolean = false) {
        mutableCanResetIdentity.value = canResetIdentity
        mutableState.value = state
    }

    private fun launchOperation(block: suspend () -> Unit) {
        operation?.cancel()
        mutableCanResetIdentity.value = false
        operation = viewModelScope.launch(dispatcher) { block() }
    }

    override fun onCleared() {
        operation?.cancel()
        super.onCleared()
    }
}
