package com.framearchive.mobile.network

import com.framearchive.mobile.crypto.PublicJwk
import com.framearchive.mobile.pairing.DeviceAuthApiFactory
import com.framearchive.mobile.pairing.DeviceKeyOperations
import com.framearchive.mobile.pairing.PairingLink
import com.framearchive.mobile.pairing.PairingRepository
import com.framearchive.mobile.storage.DeviceIdentity
import com.framearchive.mobile.storage.IdentityStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class DeviceAuthApiTest {
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun pairSendsAndroidClientType() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(202).setBody(
                """{"pairing_id":11,"device_id":22,"claim_secret":"secret","verification_code":"ABCD"}""",
            ),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val claim = api.requestPairing("pair-token", "Living room", PublicJwk(x = "x-value", y = "y-value"))

        assertEquals(PairClaim(11, 22, "secret", "ABCD"), claim)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/device-auth/pair", request.path)
        val body = JSONObject(request.body.readUtf8())
        assertEquals(setOf("token", "name", "public_key", "client_type"), body.keys().asSequence().toSet())
        assertEquals("pair-token", body.getString("token"))
        assertEquals("Living room", body.getString("name"))
        assertEquals("android", body.getString("client_type"))
        val jwk = body.getJSONObject("public_key")
        assertEquals(setOf("kty", "crv", "x", "y"), jwk.keys().asSequence().toSet())
        assertEquals("EC", jwk.getString("kty"))
        assertEquals("P-256", jwk.getString("crv"))
        assertEquals("x-value", jwk.getString("x"))
        assertEquals("y-value", jwk.getString("y"))
    }

    @Test
    fun verifyCapturesOnlyFrameDeviceSessionCookie() = runTest {
        server.enqueue(
            MockResponse()
                .setBody("{}")
                .addHeader("Set-Cookie", "analytics=ignored; Path=/")
                .addHeader("Set-Cookie", "frame_device_session=session-value; Path=/; HttpOnly; SameSite=Lax"),
        )
        val signature = ByteArray(64) { it.toByte() }
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val session = api.verify(22, "challenge-text", signature)

        assertEquals(
            "frame_device_session=session-value; Path=/; HttpOnly; SameSite=Lax",
            session.setCookieHeader,
        )
        val request = server.takeRequest()
        assertEquals("/api/device-auth/verify", request.path)
        val body = JSONObject(request.body.readUtf8())
        assertEquals("challenge-text", body.getString("challenge"))
        assertArrayEquals(signature, Base64.getUrlDecoder().decode(body.getString("signature")))
    }

    @Test
    fun verifyRejectsMissingTargetCookie() = runTest {
        server.enqueue(MockResponse().setBody("{}").addHeader("Set-Cookie", "other=value; Path=/"))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> {
            api.verify(1, "challenge", ByteArray(64))
        }
    }

    @Test
    fun verifyRejectsConflictingTargetCookies() = runTest {
        server.enqueue(
            MockResponse().setBody("{}")
                .addHeader("Set-Cookie", "frame_device_session=one; Path=/")
                .addHeader("Set-Cookie", "frame_device_session=two; Path=/"),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> {
            api.verify(1, "challenge", ByteArray(64))
        }
    }

    @Test
    fun verifyRejectsIdenticalDuplicateTargetCookies() = runTest {
        val duplicate = "frame_device_session=same; Path=/; HttpOnly"
        server.enqueue(
            MockResponse().setBody("{}")
                .addHeader("Set-Cookie", duplicate)
                .addHeader("Set-Cookie", duplicate),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> {
            api.verify(1, "challenge", ByteArray(64))
        }
    }

    @Test
    fun verify401MapsToDeviceRevokedWithoutLeakingChallenge() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"bad challenge secret-c"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<DeviceRevoked> {
            api.verify(1, "secret-c", ByteArray(64))
        }

        assertFalse(error.message.orEmpty().contains("secret-c"))
    }

    @Test
    fun redirectToDifferentOriginIsRejected() = runTest {
        val destination = MockWebServer().apply { start() }
        try {
            server.enqueue(
                MockResponse()
                    .setResponseCode(302)
                    .addHeader("Location", destination.url("/stolen?claim_secret=do-not-leak")),
            )
            val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

            val error = assertSuspendThrows<DeviceAuthApiException> {
                api.pairingStatus(11, "top-secret")
            }

            assertNull(destination.takeRequest(200, java.util.concurrent.TimeUnit.MILLISECONDS))
            assertFalse(error.message.orEmpty().contains("claim_secret"))
            assertFalse(error.message.orEmpty().contains("do-not-leak"))
            assertFalse(error.message.orEmpty().contains("top-secret"))
        } finally {
            destination.shutdown()
        }
    }

    @Test
    fun apiErrorUsesFastApiDetailMessage() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(400).setBody("""{"detail":"Device name is invalid"}"""),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<DeviceAuthApiException> {
            api.requestPairing("sensitive-token", "name", PublicJwk(x = "x", y = "y"))
        }

        assertEquals("Device name is invalid", error.message)
        assertFalse(error.message.orEmpty().contains("sensitive-token"))
    }

    @Test
    fun pairingStatusUsesFixedPathAndMapsAllKnownStatuses() = runTest {
        listOf("pending", "open", "claiming", "approved", "rejected", "expired").forEach {
            server.enqueue(MockResponse().setBody("""{"status":"$it"}"""))
        }
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertEquals(PairingStatus.Pending, api.pairingStatus(9, "secret"))
        assertEquals(PairingStatus.Pending, api.pairingStatus(9, "secret"))
        assertEquals(PairingStatus.Pending, api.pairingStatus(9, "secret"))
        assertEquals(PairingStatus.Approved, api.pairingStatus(9, "secret"))
        assertEquals(PairingStatus.Rejected, api.pairingStatus(9, "secret"))
        assertEquals(PairingStatus.Expired, api.pairingStatus(9, "secret"))
        repeat(6) {
            val request = server.takeRequest()
            assertEquals("/api/device-auth/pair-status", request.path)
            val body = JSONObject(request.body.readUtf8())
            assertEquals(9L, body.getLong("pairing_id"))
            assertEquals("secret", body.getString("claim_secret"))
        }
    }

    @Test
    fun pairingStatusRejectsUnknownStatus() = runTest {
        server.enqueue(MockResponse().setBody("""{"status":"surprise"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> { api.pairingStatus(1, "secret") }
    }

    @Test
    fun pairingStatusHttpTerminalsMapToTypedErrors() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"gone"}"""))
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"no"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<PairingExpired> { api.pairingStatus(1, "secret") }
        assertSuspendThrows<PairingRejected> { api.pairingStatus(1, "secret") }
    }

    @Test
    fun challengeUsesFixedPathAndParsesValue() = runTest {
        server.enqueue(MockResponse().setBody("""{"challenge":"AQID"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertEquals("AQID", api.challenge(52))
        val request = server.takeRequest()
        assertEquals("/api/device-auth/challenge", request.path)
        assertEquals(52L, JSONObject(request.body.readUtf8()).getLong("device_id"))
    }

    @Test
    fun challengeRejectsNonBase64UrlValue() = runTest {
        server.enqueue(MockResponse().setBody("""{"challenge":"not+base64"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> { api.challenge(52) }
    }

    @Test
    fun isAuthenticatedRequiresBoolean() = runTest {
        server.enqueue(MockResponse().setBody("""{"authenticated":true}"""))
        server.enqueue(MockResponse().setBody("""{"authenticated":"true"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertTrue(api.isAuthenticated())
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/device-auth/status", request.path)
        assertSuspendThrows<DeviceAuthApiException> { api.isAuthenticated() }
    }

    @Test
    fun ioExceptionMapsToNetworkUnavailable() = runTest {
        val client = OkHttpClient.Builder().addInterceptor { throw IOException("offline secret-url") }.build()
        val api = DeviceAuthApi(server.url("/").toString(), client)

        val error = assertSuspendThrows<NetworkUnavailable> { api.isAuthenticated() }
        assertEquals("Network unavailable", error.message)
    }

    @Test
    fun cancellationExceptionIsNotWrapped() = runTest {
        val cancellation = CancellationException("cancelled")
        val client = OkHttpClient.Builder().addInterceptor { throw cancellation }.build()
        val api = DeviceAuthApi(server.url("/").toString(), client)

        assertSuspendThrows<CancellationException> { api.isAuthenticated() }
    }

    @Test
    fun cancellingInFlightRequestCancelsOkHttpCall() = runBlocking {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val client = OkHttpClient()
        val api = DeviceAuthApi(server.url("/").toString(), client)
        val request = async(Dispatchers.Default) { api.isAuthenticated() }
        assertTrue(server.takeRequest(2, TimeUnit.SECONDS) != null)

        request.cancel(CancellationException("stop in flight"))

        assertSuspendThrows<CancellationException> { request.await() }
        withTimeout(2_000) {
            request.join()
            while (client.dispatcher.runningCallsCount() != 0) delay(10)
        }
        assertTrue(request.isCancelled)
        assertEquals(0, client.dispatcher.runningCallsCount())
    }

    @Test
    fun constructorRejectsNonOriginServerUrl() {
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) {
            DeviceAuthApi("https://example.com/attacker/path", OkHttpClient())
        }
    }

    @Test
    fun approvedPairingAuthenticatesWithRawSignature() = runTest {
        val challengeBytes = byteArrayOf(0, 1, 2, 3, 127, -1)
        val challenge = Base64.getUrlEncoder().withoutPadding().encodeToString(challengeBytes)
        val signature = ByteArray(64) { (it + 7).toByte() }
        val store = FakeIdentityStore()
        val keys = FakeKeys(signature)
        val api = FakeApi().apply {
            claim = PairClaim(71, 72, "claim-secret", "VERIFY")
            statuses.add(PairingStatus.Approved)
            challengeValue = challenge
        }
        var now = 1_000L
        val repository = PairingRepository(
            identityStore = store,
            keyOps = keys,
            apiFactory = DeviceAuthApiFactory { api },
            nowEpochMillis = { now },
            pollDelay = { now += it },
            aliasFactory = { "device-key" },
        )

        val pending = repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        assertEquals("claim-secret", pending.pending?.claimSecret)
        assertEquals(pending, store.value)

        val session = repository.awaitApproval(pending)

        assertEquals(AuthSession("frame_device_session=value; Path=/"), session)
        assertArrayEquals(challengeBytes, keys.lastChallenge)
        assertArrayEquals(signature, api.verifiedSignature)
        assertEquals("Pixel", store.value?.deviceName)
        assertNull(store.value?.pending)
        assertFalse(store.saved.last().toString().contains("claim-secret"))
    }

    @Test
    fun rejectedPairingClearsIdentityAndKey() = runTest {
        val fixture = repositoryFixture(PairingStatus.Rejected)
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        assertSuspendThrows<PairingRejected> { fixture.repository.awaitApproval(identity) }

        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
        assertEquals(listOf("new-key"), fixture.keys.deleted)
    }

    @Test
    fun expiredPairingClearsIdentityAndKey() = runTest {
        val fixture = repositoryFixture(PairingStatus.Expired)
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        assertSuspendThrows<PairingExpired> { fixture.repository.awaitApproval(identity) }

        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
    }

    @Test
    fun serverExpiredErrorClearsIdentityAndKey() = runTest {
        val fixture = repositoryFixture()
        fixture.api.statusError = PairingExpired("gone")
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        assertSuspendThrows<PairingExpired> { fixture.repository.awaitApproval(identity) }

        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
    }

    @Test
    fun networkFailureWhilePollingPreservesPendingIdentityAndKey() = runTest {
        val fixture = repositoryFixture()
        fixture.api.statusError = NetworkUnavailable(IOException("offline"))
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        assertSuspendThrows<NetworkUnavailable> { fixture.repository.awaitApproval(identity) }

        assertEquals(identity, fixture.store.value)
        assertTrue("new-key" in fixture.keys.aliases)
        assertTrue(fixture.keys.deleted.isEmpty())
    }

    @Test
    fun localExpiryStopsWithoutPolling() = runTest {
        val fixture = repositoryFixture()
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.now = identity.pending!!.expiresAtEpochMillis

        assertSuspendThrows<PairingExpired> { fixture.repository.awaitApproval(identity) }

        assertEquals(0, fixture.api.statusCalls)
        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
    }

    @Test
    fun pollingUsesInjectedDelay() = runTest {
        val fixture = repositoryFixture(PairingStatus.Pending, PairingStatus.Approved)
        fixture.api.challengeValue = Base64.getUrlEncoder().withoutPadding().encodeToString(byteArrayOf(1))
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        fixture.repository.awaitApproval(identity)

        assertEquals(listOf(1_500L), fixture.delays)
    }

    @Test
    fun submitFailureDeletesOnlyNewAlias() = runTest {
        val fixture = repositoryFixture()
        fixture.api.requestError = DeviceAuthApiException("failed")

        assertSuspendThrows<DeviceAuthApiException> {
            fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        }

        assertEquals(listOf("new-key"), fixture.keys.deleted)
        assertNull(fixture.store.value)
    }

    @Test
    fun aliasCollisionRetriesWithoutDeletingExistingKey() = runTest {
        val fixture = repositoryFixture()
        fixture.keys.aliases += "old-key"
        val aliases = ArrayDeque(listOf("old-key", "new-key"))
        fixture.aliasProvider = { aliases.removeFirst() }

        val identity = fixture.newRepository().submit(PairingLink("https://example.com", "token"), "Pixel")

        assertEquals("new-key", identity.keyAlias)
        assertTrue("old-key" in fixture.keys.aliases)
        assertFalse("old-key" in fixture.keys.deleted)
    }

    @Test
    fun aliasCheckAndCreationAreLockedAcrossRepositoryInstances() = runBlocking {
        val keys = RacingKeys()
        val firstApi = FakeApi().apply {
            claim = PairClaim(1, 1, "first-secret", "FIRST")
        }
        val secondApi = FakeApi().apply {
            requestError = DeviceAuthApiException("second submit failed")
        }
        val secondAliases = ArrayDeque(listOf("race-key", "second-key"))
        val firstRepository = PairingRepository(
            identityStore = FakeIdentityStore(),
            keyOps = keys,
            apiFactory = DeviceAuthApiFactory { firstApi },
            nowEpochMillis = { 1_000L },
            pollDelay = {},
            aliasFactory = { "race-key" },
        )
        val secondRepository = PairingRepository(
            identityStore = FakeIdentityStore(),
            keyOps = keys,
            apiFactory = DeviceAuthApiFactory { secondApi },
            nowEpochMillis = { 1_000L },
            pollDelay = {},
            aliasFactory = { secondAliases.removeFirst() },
        )

        supervisorScope {
            val first = async(Dispatchers.Default) {
                firstRepository.submit(PairingLink("https://example.com", "token-1"), "First")
            }
            assertTrue(keys.firstCreateEntered.await(2, TimeUnit.SECONDS))
            val second = async(Dispatchers.Default) {
                secondRepository.submit(PairingLink("https://example.com", "token-2"), "Second")
            }
            delay(150)
            val containsCallsWhileFirstCreateBlocked = keys.containsCalls.get()
            keys.releaseFirstCreate.countDown()

            assertEquals("race-key", withTimeout(2_000) { first.await() }.keyAlias)
            assertSuspendThrows<DeviceAuthApiException> { withTimeout(2_000) { second.await() } }
            assertEquals(1, containsCallsWhileFirstCreateBlocked)
        }
        assertTrue("race-key" in keys.aliases)
        assertFalse("race-key" in keys.deleted)
        assertEquals(listOf("second-key"), keys.deleted)
    }

    @Test
    fun cancellationDuringSubmitCleansNewAliasAndPropagates() = runTest {
        val fixture = repositoryFixture()
        val cancellation = CancellationException("stop")
        fixture.api.requestError = cancellation

        val error = assertSuspendThrows<CancellationException> {
            fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        }

        assertTrue(error === cancellation)
        assertEquals(listOf("new-key"), fixture.keys.deleted)
    }

    @Test
    fun invalidSignerOutputClearsPermanentIdentity() = runTest {
        val fixture = RepositoryFixture(signature = ByteArray(63))
        fixture.api.claim = PairClaim(71, 72, "claim-secret", "VERIFY")
        fixture.api.challengeValue = "AQ"
        fixture.repository = fixture.newRepository()
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.store.save(identity.copy(pending = null))

        assertSuspendThrows<IllegalArgumentException> {
            fixture.repository.authenticate(identity.copy(pending = null))
        }

        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
    }

    @Test
    fun cancelSurfacesCleanupFailureAndStillAttemptsKeyDeletion() = runTest {
        val fixture = repositoryFixture()
        fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.store.clearError = IllegalStateException("clear failed")

        val error = org.junit.Assert.assertThrows(IllegalStateException::class.java) {
            fixture.repository.cancel()
        }

        assertEquals("clear failed", error.message)
        assertEquals(listOf("new-key"), fixture.keys.deleted)
    }

    private fun repositoryFixture(vararg statuses: PairingStatus): RepositoryFixture {
        val fixture = RepositoryFixture()
        fixture.api.claim = PairClaim(71, 72, "claim-secret", "VERIFY")
        fixture.api.statuses.addAll(statuses)
        fixture.api.challengeValue = Base64.getUrlEncoder().withoutPadding().encodeToString(byteArrayOf(1, 2))
        fixture.repository = fixture.newRepository()
        return fixture
    }

    private class RepositoryFixture(signature: ByteArray = ByteArray(64)) {
        val store = FakeIdentityStore()
        val keys = FakeKeys(signature)
        val api = FakeApi()
        var now = 1_000L
        val delays = mutableListOf<Long>()
        var aliasProvider: () -> String = { "new-key" }
        lateinit var repository: PairingRepository

        fun newRepository() = PairingRepository(
            identityStore = store,
            keyOps = keys,
            apiFactory = DeviceAuthApiFactory { api },
            nowEpochMillis = { now },
            pollDelay = { delays += it },
            aliasFactory = { aliasProvider() },
        )
    }

    private class FakeIdentityStore : IdentityStore {
        var value: DeviceIdentity? = null
        val saved = mutableListOf<DeviceIdentity>()
        var clearError: Throwable? = null

        override fun read(): DeviceIdentity? = value
        override fun save(identity: DeviceIdentity) {
            value = identity
            saved += identity
        }
        override fun clear() {
            clearError?.let { throw it }
            value = null
        }
    }

    private class FakeKeys(private val signature: ByteArray) : DeviceKeyOperations {
        val aliases = mutableSetOf<String>()
        val deleted = mutableListOf<String>()
        var lastChallenge: ByteArray? = null

        override fun contains(alias: String): Boolean = alias in aliases
        override fun create(alias: String): PublicJwk {
            aliases += alias
            return PublicJwk(x = "x", y = "y")
        }
        override fun sign(alias: String, challenge: ByteArray): ByteArray {
            lastChallenge = challenge
            return signature
        }
        override fun delete(alias: String) {
            deleted += alias
            aliases -= alias
        }
    }

    private class RacingKeys : DeviceKeyOperations {
        val aliases = java.util.Collections.synchronizedSet(mutableSetOf<String>())
        val deleted = java.util.Collections.synchronizedList(mutableListOf<String>())
        val containsCalls = AtomicInteger()
        val firstCreateEntered = CountDownLatch(1)
        val releaseFirstCreate = CountDownLatch(1)
        private val createCalls = AtomicInteger()

        override fun contains(alias: String): Boolean {
            containsCalls.incrementAndGet()
            return alias in aliases
        }

        override fun create(alias: String): PublicJwk {
            if (alias == "race-key" && createCalls.incrementAndGet() == 1) {
                firstCreateEntered.countDown()
                check(releaseFirstCreate.await(2, TimeUnit.SECONDS)) { "Timed out releasing first key creation" }
            }
            aliases += alias
            return PublicJwk(x = "x", y = "y")
        }

        override fun sign(alias: String, challenge: ByteArray): ByteArray = ByteArray(64)

        override fun delete(alias: String) {
            deleted += alias
            aliases -= alias
        }
    }

    private class FakeApi : DeviceAuthService {
        lateinit var claim: PairClaim
        val statuses = ArrayDeque<PairingStatus>()
        lateinit var challengeValue: String
        var verifiedSignature: ByteArray? = null
        var requestError: Throwable? = null
        var statusError: Throwable? = null
        var statusCalls = 0

        override suspend fun requestPairing(token: String, name: String, jwk: PublicJwk): PairClaim {
            requestError?.let { throw it }
            return claim
        }
        override suspend fun pairingStatus(pairingId: Long, claimSecret: String): PairingStatus {
            statusCalls += 1
            statusError?.let { throw it }
            return statuses.removeFirst()
        }
        override suspend fun challenge(deviceId: Long): String = challengeValue
        override suspend fun verify(deviceId: Long, challenge: String, signature: ByteArray): AuthSession {
            verifiedSignature = signature
            return AuthSession("frame_device_session=value; Path=/")
        }
        override suspend fun isAuthenticated(): Boolean = true
    }

    private suspend inline fun <reified T : Throwable> assertSuspendThrows(
        crossinline action: suspend () -> Unit,
    ): T = try {
        action()
        throw AssertionError("Expected ${T::class.java.simpleName}")
    } catch (error: Throwable) {
        if (error !is T) throw AssertionError("Expected ${T::class.java.simpleName}", error)
        error
    }
}
