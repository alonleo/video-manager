package com.framearchive.mobile.pairing

import com.framearchive.mobile.crypto.DeviceKeyStore
import com.framearchive.mobile.crypto.PublicJwk
import com.framearchive.mobile.network.AuthSession
import com.framearchive.mobile.network.DeviceAuthApi
import com.framearchive.mobile.network.DeviceAuthApiException
import com.framearchive.mobile.network.DeviceAuthService
import com.framearchive.mobile.network.DeviceRevoked
import com.framearchive.mobile.network.PairingExpired
import com.framearchive.mobile.network.PairingRejected
import com.framearchive.mobile.network.PairingStatus
import com.framearchive.mobile.storage.DeviceIdentity
import com.framearchive.mobile.storage.IdentityStore
import com.framearchive.mobile.storage.PendingPairing
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import okhttp3.OkHttpClient
import java.util.Base64
import java.util.UUID

interface PairingCoordinator {
    fun storedIdentity(): DeviceIdentity?
    suspend fun submit(link: PairingLink, name: String): DeviceIdentity
    suspend fun awaitApproval(identity: DeviceIdentity): AuthSession
    suspend fun authenticate(identity: DeviceIdentity): AuthSession
    suspend fun resume(identity: DeviceIdentity): AuthSession
    fun cancel()
}

internal interface DeviceKeyOperations {
    fun contains(alias: String): Boolean
    fun create(alias: String): PublicJwk
    fun sign(alias: String, challenge: ByteArray): ByteArray
    fun delete(alias: String)
}

private class AndroidDeviceKeyOperations(private val keyStore: DeviceKeyStore) : DeviceKeyOperations {
    override fun contains(alias: String): Boolean = keyStore.contains(alias)
    override fun create(alias: String): PublicJwk = keyStore.create(alias)
    override fun sign(alias: String, challenge: ByteArray): ByteArray = keyStore.sign(alias, challenge)
    override fun delete(alias: String) = keyStore.delete(alias)
}

internal fun interface DeviceAuthApiFactory {
    fun create(serverOrigin: String): DeviceAuthService
}

class StaleIdentityOperation(message: String = "Device identity changed while the operation was running") :
    IllegalStateException(message)

class PairingRepository internal constructor(
    private val identityStore: IdentityStore,
    private val keyOps: DeviceKeyOperations,
    private val apiFactory: DeviceAuthApiFactory,
    private val nowEpochMillis: () -> Long,
    private val pollDelay: suspend (Long) -> Unit,
    private val aliasFactory: () -> String,
) : PairingCoordinator {
    constructor(identityStore: IdentityStore, client: OkHttpClient) : this(
        identityStore = identityStore,
        keyOps = AndroidDeviceKeyOperations(DeviceKeyStore()),
        apiFactory = DeviceAuthApiFactory { origin -> DeviceAuthApi(origin, client) },
        nowEpochMillis = System::currentTimeMillis,
        pollDelay = { delay(it) },
        aliasFactory = { "framearchive-device-auth-v1-${UUID.randomUUID()}" },
    )

    override fun storedIdentity(): DeviceIdentity? = synchronized(IDENTITY_STATE_LOCK) {
        identityStore.read()
    }

    override suspend fun submit(link: PairingLink, name: String): DeviceIdentity {
        require(name.isNotBlank()) { "Device name must not be blank" }
        val createdKey = createKey()
        val alias = createdKey.alias
        var persisted = false
        try {
            val claim = apiFactory.create(link.serverOrigin).requestPairing(link.token, name, createdKey.publicJwk)
            val identity = DeviceIdentity(
                serverOrigin = link.serverOrigin,
                deviceId = claim.deviceId,
                deviceName = name,
                keyAlias = alias,
                pending = PendingPairing(
                    pairingId = claim.pairingId,
                    claimSecret = claim.claimSecret,
                    verificationCode = claim.verificationCode,
                    // The server remains authoritative and may expire the pairing sooner.
                    expiresAtEpochMillis = nowEpochMillis() + LOCAL_PAIRING_LIFETIME_MILLIS,
                ),
            )
            synchronized(IDENTITY_STATE_LOCK) {
                if (identityStore.read() != null) {
                    throw StaleIdentityOperation("A device identity is already stored")
                }
                identityStore.save(identity)
            }
            persisted = true
            return identity
        } catch (error: Throwable) {
            if (!persisted) cleanupNewAlias(alias, error)
            throw error
        }
    }

    override suspend fun awaitApproval(identity: DeviceIdentity): AuthSession {
        identity.pending
            ?: throw IllegalArgumentException("Device identity is not awaiting approval")
        val api = apiFactory.create(identity.serverOrigin)
        while (true) {
            val current = currentPending(identity)
            val pending = current.pending!!
            if (nowEpochMillis() >= pending.expiresAtEpochMillis) {
                val expired = PairingExpired()
                clearPendingOrThrowStale(identity, expired)
                throw expired
            }
            val status = try {
                api.pairingStatus(pending.pairingId, pending.claimSecret)
            } catch (error: PairingExpired) {
                clearPendingOrThrowStale(identity, error)
                throw error
            } catch (error: PairingRejected) {
                clearPendingOrThrowStale(identity, error)
                throw error
            }
            when (status) {
                PairingStatus.Pending -> pollDelay(POLL_INTERVAL_MILLIS)
                PairingStatus.Approved -> {
                    val approved = approvePendingOrThrowStale(identity)
                    return authenticate(approved)
                }
                PairingStatus.Rejected -> {
                    val rejected = PairingRejected()
                    clearPendingOrThrowStale(identity, rejected)
                    throw rejected
                }
                PairingStatus.Expired -> {
                    val expired = PairingExpired()
                    clearPendingOrThrowStale(identity, expired)
                    throw expired
                }
            }
        }
    }

    override suspend fun authenticate(identity: DeviceIdentity): AuthSession {
        val expected = currentApproved(identity)
        if (!keyOps.contains(expected.keyAlias)) {
            val missing = IllegalStateException("Device authentication key is unavailable")
            clearApprovedOrThrowStale(expected, missing)
            throw missing
        }
        val api = apiFactory.create(expected.serverOrigin)
        try {
            val challenge = api.challenge(expected.deviceId)
            val challengeBytes = decodeChallenge(challenge)
            val signature = try {
                keyOps.sign(expected.keyAlias, challengeBytes)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                clearApprovedOrThrowStale(expected, error)
                throw error
            }
            if (signature.size != P1363_SIGNATURE_BYTES) {
                val invalid = IllegalArgumentException("Device key returned an invalid P1363 signature")
                clearApprovedOrThrowStale(expected, invalid)
                throw invalid
            }
            val session = api.verify(expected.deviceId, challenge, signature)
            synchronized(IDENTITY_STATE_LOCK) {
                if (!matchesApproved(identityStore.read(), expected)) throw StaleIdentityOperation()
            }
            return session
        } catch (error: DeviceRevoked) {
            clearApprovedOrThrowStale(expected, error)
            throw error
        }
    }

    override suspend fun resume(identity: DeviceIdentity): AuthSession =
        if (identity.pending != null) awaitApproval(identity) else authenticate(identity)

    override fun cancel() {
        synchronized(IDENTITY_STATE_LOCK) {
            val identity = identityStore.read() ?: return
            var failure: Throwable? = null
            try {
                identityStore.clear()
            } catch (cleanup: Throwable) {
                failure = cleanup
            }
            try {
                keyOps.delete(identity.keyAlias)
            } catch (cleanup: Throwable) {
                failure?.addSuppressed(cleanup) ?: run { failure = cleanup }
            }
            failure?.let { throw it }
        }
    }

    private fun createKey(): CreatedKey = synchronized(ALIAS_CREATION_LOCK) {
        repeat(MAX_ALIAS_ATTEMPTS) {
            val alias = aliasFactory()
            require(alias.isNotBlank()) { "Key alias must not be blank" }
            if (!keyOps.contains(alias)) {
                try {
                    return@synchronized CreatedKey(alias, keyOps.create(alias))
                } catch (error: Throwable) {
                    cleanupNewAlias(alias, error)
                    throw error
                }
            }
        }
        throw DeviceAuthApiException("Unable to allocate a device key alias")
    }

    private fun cleanupNewAlias(alias: String, original: Throwable) {
        try {
            keyOps.delete(alias)
        } catch (cleanup: Throwable) {
            original.addSuppressed(cleanup)
        }
    }

    private fun currentPending(expected: DeviceIdentity): DeviceIdentity = synchronized(IDENTITY_STATE_LOCK) {
        identityStore.read()?.takeIf { matchesPending(it, expected) } ?: throw StaleIdentityOperation()
    }

    private fun currentApproved(expected: DeviceIdentity): DeviceIdentity = synchronized(IDENTITY_STATE_LOCK) {
        identityStore.read()?.takeIf { matchesApproved(it, expected) } ?: throw StaleIdentityOperation()
    }

    private fun approvePendingOrThrowStale(expected: DeviceIdentity): DeviceIdentity =
        synchronized(IDENTITY_STATE_LOCK) {
            val current = identityStore.read()?.takeIf { matchesPending(it, expected) }
                ?: throw StaleIdentityOperation()
            val approved = current.copy(pending = null)
            identityStore.save(approved)
            approved
        }

    private fun clearPendingOrThrowStale(expected: DeviceIdentity, original: Throwable) {
        synchronized(IDENTITY_STATE_LOCK) {
            val current = identityStore.read()?.takeIf { matchesPending(it, expected) }
                ?: throw StaleIdentityOperation()
            clearLocked(current, original)
        }
    }

    private fun clearApprovedOrThrowStale(expected: DeviceIdentity, original: Throwable) {
        synchronized(IDENTITY_STATE_LOCK) {
            val current = identityStore.read()?.takeIf { matchesApproved(it, expected) }
                ?: throw StaleIdentityOperation()
            clearLocked(current, original)
        }
    }

    private fun clearLocked(current: DeviceIdentity, original: Throwable) {
        try {
            identityStore.clear()
        } catch (cleanup: Throwable) {
            original.addSuppressed(cleanup)
        }
        try {
            keyOps.delete(current.keyAlias)
        } catch (cleanup: Throwable) {
            original.addSuppressed(cleanup)
        }
    }

    private fun matchesPending(current: DeviceIdentity?, expected: DeviceIdentity): Boolean =
        matchesIdentity(current, expected) &&
            current?.pending?.pairingId == expected.pending?.pairingId &&
            expected.pending != null

    private fun matchesApproved(current: DeviceIdentity?, expected: DeviceIdentity): Boolean =
        matchesIdentity(current, expected) && current?.pending == null && expected.pending == null

    private fun matchesIdentity(current: DeviceIdentity?, expected: DeviceIdentity): Boolean =
        current?.serverOrigin == expected.serverOrigin &&
            current.deviceId == expected.deviceId &&
            current.keyAlias == expected.keyAlias

    private fun decodeChallenge(challenge: String): ByteArray {
        if (challenge.isEmpty() || challenge.any { it !in BASE64URL_CHARACTERS }) {
            throw DeviceAuthApiException("Authentication challenge is invalid")
        }
        val decoded = try {
            Base64.getUrlDecoder().decode(challenge)
        } catch (error: IllegalArgumentException) {
            throw DeviceAuthApiException("Authentication challenge is invalid", error)
        }
        if (decoded.isEmpty() || Base64.getUrlEncoder().withoutPadding().encodeToString(decoded) != challenge) {
            throw DeviceAuthApiException("Authentication challenge is invalid")
        }
        return decoded
    }

    private companion object {
        const val LOCAL_PAIRING_LIFETIME_MILLIS = 120_000L
        const val POLL_INTERVAL_MILLIS = 1_500L
        const val MAX_ALIAS_ATTEMPTS = 16
        const val P1363_SIGNATURE_BYTES = 64
        val BASE64URL_CHARACTERS = ('A'..'Z') + ('a'..'z') + ('0'..'9') + setOf('-', '_')
        val ALIAS_CREATION_LOCK = Any()
        val IDENTITY_STATE_LOCK = Any()
    }

    private data class CreatedKey(val alias: String, val publicJwk: PublicJwk)
}
