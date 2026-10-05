package com.framearchive.mobile.network

import com.framearchive.mobile.crypto.PublicJwk
import com.framearchive.mobile.pairing.DeviceAuthApiFactory
import com.framearchive.mobile.pairing.DeviceKeyOperations
import com.framearchive.mobile.pairing.IdentityCleanupFailed
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
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
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
        val request = takeRequest()
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
    fun secretDtosRedactTheirStringRepresentations() {
        val claimSecret = "claim-secret-must-never-appear"
        val cookieValue = "cookie-value-must-never-appear"

        assertFalse(PairClaim(1, 2, claimSecret, "CODE").toString().contains(claimSecret))
        assertFalse(
            AuthSession("frame_device_session=$cookieValue; Path=/; HttpOnly")
                .toString()
                .contains(cookieValue),
        )
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
        val request = takeRequest()
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
    fun verifyRejectsEmptyTargetCookieValue() = runTest {
        server.enqueue(
            MockResponse().setBody("{}")
                .addHeader("Set-Cookie", "frame_device_session=; Path=/; HttpOnly"),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<DeviceAuthApiException> {
            api.verify(1, "challenge", ByteArray(64))
        }
    }

    @Test
    fun sessionCookieParserRejectsControlCharacters() {
        assertNull(parseSessionCookiePair("frame_device_session=bad\u0001value; Path=/"))
    }

    @Test
    fun verifySessionCookieIsSentOnlyToLaterStatusRequest() = runTest {
        server.enqueue(
            MockResponse().setBody("{}")
                .addHeader("Set-Cookie", "unrelated=ignored; Path=/")
                .addHeader("Set-Cookie", "frame_device_session=session-value; Path=/; HttpOnly"),
        )
        server.enqueue(MockResponse().setBody("""{"authenticated":true}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        api.verify(1, "challenge", ByteArray(64))
        assertTrue(api.isAuthenticated())

        val verifyRequest = takeRequest()
        val statusRequest = takeRequest()
        assertNull(verifyRequest.getHeader("Cookie"))
        assertEquals("frame_device_session=session-value", statusRequest.getHeader("Cookie"))
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
    fun oversizedPair409StillMapsToPairingExpired() = runTest {
        server.enqueue(MockResponse().setResponseCode(409).setBody("x".repeat(65_537)))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<PairingExpired> {
            api.requestPairing("sensitive-token", "name", PublicJwk(x = "x", y = "y"))
        }

        assertEquals("Device authentication request failed", error.message)
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
            val request = takeRequest()
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
    fun oversizedPairingStatusErrorsKeepTypedMapping() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("x".repeat(65_537)))
        server.enqueue(
            MockResponse().setResponseCode(409)
                .setChunkedBody("x".repeat(65_537), 1_024),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertSuspendThrows<PairingExpired> { api.pairingStatus(1, "secret") }
        assertSuspendThrows<PairingRejected> { api.pairingStatus(1, "secret") }
    }

    @Test
    fun oversizedVerify401StillMapsToDeviceRevoked() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(401)
                .setChunkedBody("x".repeat(65_537), 1_024),
        )
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<DeviceRevoked> {
            api.verify(1, "sensitive-challenge", ByteArray(64))
        }

        assertEquals("Device authentication request failed", error.message)
    }

    @Test
    fun challengeUsesFixedPathAndParsesValue() = runTest {
        server.enqueue(MockResponse().setBody("""{"challenge":"AQID"}"""))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        assertEquals("AQID", api.challenge(52))
        val request = takeRequest()
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
        val request = takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/device-auth/status", request.path)
        assertNull(request.getHeader("Cookie"))
        assertSuspendThrows<DeviceAuthApiException> { api.isAuthenticated() }
    }

    @Test
    fun contentLengthResponseOver64KiBIsRejectedBeforeParsing() = runTest {
        server.enqueue(MockResponse().setBody("x".repeat(65_537)))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<DeviceAuthApiException> { api.isAuthenticated() }

        assertEquals("Device authentication response exceeds 64 KiB", error.message)
    }

    @Test
    fun chunkedResponseOver64KiBIsRejectedWhileStreaming() = runTest {
        server.enqueue(MockResponse().setChunkedBody("x".repeat(65_537), 1_024))
        val api = DeviceAuthApi(server.url("/").toString(), OkHttpClient())

        val error = assertSuspendThrows<DeviceAuthApiException> { api.isAuthenticated() }

        assertEquals("Device authentication response exceeds 64 KiB", error.message)
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
    fun cancellingAfterHeadersCancelsSlowResponseBodyRead() = runBlocking {
        val headersReturned = CountDownLatch(1)
        val responseBodyFailed = CountDownLatch(1)
        server.enqueue(
            MockResponse()
                .setBody("""{"authenticated":true,"padding":"${"x".repeat(256)}"}""")
                .throttleBody(1, 250, TimeUnit.MILLISECONDS),
        )
        val client = OkHttpClient.Builder()
            .addNetworkInterceptor { chain ->
                chain.proceed(chain.request()).also { headersReturned.countDown() }
            }
            .eventListener(
                object : EventListener() {
                    override fun responseFailed(call: Call, ioe: IOException) {
                        responseBodyFailed.countDown()
                    }
                },
            )
            .build()
        val api = DeviceAuthApi(server.url("/").toString(), client)
        val request = async(Dispatchers.Default) { api.isAuthenticated() }
        assertTrue(headersReturned.await(2, TimeUnit.SECONDS))

        request.cancel(CancellationException("stop slow body"))

        withTimeout(2_000) { request.join() }
        assertSuspendThrows<CancellationException> { request.await() }
        assertTrue(responseBodyFailed.await(2, TimeUnit.SECONDS))
        withTimeout(2_000) {
            while (client.dispatcher.runningCallsCount() != 0) delay(10)
        }
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
    fun waitForApprovalStopsBeforeChallengeAuthentication() = runTest {
        val fixture = repositoryFixture(PairingStatus.Approved)
        val pending = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")

        val approved = fixture.repository.waitForApproval(pending)

        assertNull(approved.pending)
        assertEquals(approved, fixture.store.value)
        assertEquals(0, fixture.api.challengeCalls)
        assertEquals(0, fixture.api.verifyCalls)
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
    fun staleRejectedPairingDoesNotClearReplacementIdentityOrKeys() = runTest {
        val fixture = repositoryFixture(PairingStatus.Rejected)
        val stale = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        val replacement = replacementIdentity()
        fixture.keys.aliases += replacement.keyAlias
        fixture.store.save(replacement)

        assertSuspendThrows<IllegalStateException> { fixture.repository.awaitApproval(stale) }

        assertEquals(replacement, fixture.store.value)
        assertTrue(stale.keyAlias in fixture.keys.aliases)
        assertTrue(replacement.keyAlias in fixture.keys.aliases)
        assertTrue(fixture.keys.deleted.isEmpty())
    }

    @Test
    fun staleExpiredPairingDoesNotClearReplacementIdentityOrKeys() = runTest {
        val fixture = repositoryFixture(PairingStatus.Expired)
        val stale = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        val replacement = replacementIdentity()
        fixture.keys.aliases += replacement.keyAlias
        fixture.store.save(replacement)

        assertSuspendThrows<IllegalStateException> { fixture.repository.awaitApproval(stale) }

        assertEquals(replacement, fixture.store.value)
        assertTrue(stale.keyAlias in fixture.keys.aliases)
        assertTrue(replacement.keyAlias in fixture.keys.aliases)
        assertTrue(fixture.keys.deleted.isEmpty())
    }

    @Test
    fun staleApprovedPairingDoesNotOverwriteReplacementIdentity() = runTest {
        val fixture = repositoryFixture(PairingStatus.Approved)
        val stale = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        val replacement = replacementIdentity()
        fixture.keys.aliases += replacement.keyAlias
        fixture.store.save(replacement)

        assertSuspendThrows<IllegalStateException> { fixture.repository.awaitApproval(stale) }

        assertEquals(replacement, fixture.store.value)
        assertTrue(fixture.keys.deleted.isEmpty())
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
            val secondStarted = CountDownLatch(1)
            val second = async(Dispatchers.Default) {
                secondStarted.countDown()
                secondRepository.submit(PairingLink("https://example.com", "token-2"), "Second")
            }
            assertTrue(secondStarted.await(2, TimeUnit.SECONDS))
            val secondReachedContains = keys.secondContainsEntered.await(250, TimeUnit.MILLISECONDS)
            val containsCallsWhileFirstCreateBlocked = keys.containsCalls.get()
            keys.releaseFirstCreate.countDown()

            assertEquals("race-key", withTimeout(2_000) { first.await() }.keyAlias)
            assertSuspendThrows<DeviceAuthApiException> { withTimeout(2_000) { second.await() } }
            assertFalse(secondReachedContains)
            assertEquals(1, containsCallsWhileFirstCreateBlocked)
        }
        assertTrue("race-key" in keys.aliases)
        assertFalse("race-key" in keys.deleted)
        assertEquals(listOf("second-key"), keys.deleted)
    }

    @Test
    fun concurrentSubmitsPersistOnlyOneIdentityAndCleanLosingKey() = runBlocking {
        val store = BlockingFirstSaveIdentityStore()
        val keys = FakeKeys(ByteArray(64))
        val firstApi = FakeApi().apply { claim = PairClaim(1, 1, "first-secret", "FIRST") }
        val secondApi = FakeApi().apply { claim = PairClaim(2, 2, "second-secret", "SECOND") }
        val firstRepository = repository(store, keys, firstApi, "first-key")
        val secondRepository = repository(store, keys, secondApi, "second-key")

        supervisorScope {
            val first = async(Dispatchers.Default) {
                firstRepository.submit(PairingLink("https://example.com", "token-1"), "First")
            }
            assertTrue(store.firstSaveEntered.await(2, TimeUnit.SECONDS))
            val secondStarted = CountDownLatch(1)
            val second = async(Dispatchers.Default) {
                secondStarted.countDown()
                secondRepository.submit(PairingLink("https://example.com", "token-2"), "Second")
            }
            assertTrue(secondStarted.await(2, TimeUnit.SECONDS))
            store.releaseFirstSave.countDown()

            val winner = withTimeout(2_000) { first.await() }
            assertSuspendThrows<IllegalStateException> { withTimeout(2_000) { second.await() } }
            assertEquals(winner, store.read())
        }
        assertTrue("first-key" in keys.aliases)
        assertFalse("second-key" in keys.aliases)
        assertEquals(listOf("second-key"), keys.deleted)
    }

    @Test
    fun staleAuthenticationCompletionDoesNotReturnSessionOrClearReplacement() = runBlocking {
        val store = FakeIdentityStore()
        val keys = FakeKeys(ByteArray(64))
        val api = FakeApi().apply {
            challengeValue = "AQ"
            verifyEntered = CountDownLatch(1)
            releaseVerify = CountDownLatch(1)
        }
        val repository = repository(store, keys, api, "unused-key")
        val stale = DeviceIdentity("https://example.com", 10, "Old", "old-key", pending = null)
        val replacement = replacementIdentity()
        keys.aliases += stale.keyAlias
        keys.aliases += replacement.keyAlias
        store.save(stale)

        supervisorScope {
            val authentication = async(Dispatchers.Default) { repository.authenticate(stale) }
            assertTrue(api.verifyEntered!!.await(2, TimeUnit.SECONDS))
            store.save(replacement)
            api.releaseVerify!!.countDown()

            assertSuspendThrows<IllegalStateException> {
                withTimeout(2_000) { authentication.await() }
            }
        }
        assertEquals(replacement, store.value)
        assertTrue(keys.deleted.isEmpty())
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
    fun keyDeleteFailureKeepsIdentityAndRetryCompletesCleanup() = runTest {
        val fixture = repositoryFixture()
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.keys.deleteError = IllegalStateException("delete failed")

        val error = org.junit.Assert.assertThrows(IdentityCleanupFailed::class.java) {
            fixture.repository.cancel()
        }

        assertEquals("Device identity cleanup failed", error.message)
        assertEquals(identity, fixture.store.value)
        assertTrue("new-key" in fixture.keys.aliases)

        fixture.keys.deleteError = null
        fixture.repository.cancel()

        assertNull(fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)
    }

    @Test
    fun identityClearFailureKeepsIdentityAfterKeyDeletionAndRetryCompletesCleanup() = runTest {
        val fixture = repositoryFixture()
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.store.clearError = IllegalStateException("clear failed")

        assertThrowsIdentityCleanup { fixture.repository.cancel() }

        assertEquals(identity, fixture.store.value)
        assertFalse("new-key" in fixture.keys.aliases)

        fixture.store.clearError = null
        fixture.repository.cancel()

        assertNull(fixture.store.value)
        assertEquals(listOf("new-key", "new-key"), fixture.keys.deleted)
    }

    @Test
    fun rejectedPairingCleanupFailureDoesNotMasqueradeAsTerminalSuccess() = runTest {
        val fixture = repositoryFixture(PairingStatus.Rejected)
        val identity = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        fixture.keys.deleteError = IllegalStateException("delete failed")

        assertSuspendThrows<IdentityCleanupFailed> { fixture.repository.awaitApproval(identity) }

        assertEquals(identity, fixture.store.value)
        assertTrue("new-key" in fixture.keys.aliases)
        fixture.keys.deleteError = null
        fixture.repository.cancel()
        assertNull(fixture.store.value)
    }

    @Test
    fun revokedDeviceCleanupFailureDoesNotMasqueradeAsRevocationCompletion() = runTest {
        val fixture = repositoryFixture()
        val pending = fixture.repository.submit(PairingLink("https://example.com", "token"), "Pixel")
        val approved = pending.copy(pending = null)
        fixture.store.save(approved)
        fixture.api.verifyError = DeviceRevoked()
        fixture.keys.deleteError = IllegalStateException("delete failed")

        assertSuspendThrows<IdentityCleanupFailed> { fixture.repository.authenticate(approved) }

        assertEquals(approved, fixture.store.value)
        assertTrue("new-key" in fixture.keys.aliases)
    }

    private fun repositoryFixture(vararg statuses: PairingStatus): RepositoryFixture {
        val fixture = RepositoryFixture()
        fixture.api.claim = PairClaim(71, 72, "claim-secret", "VERIFY")
        fixture.api.statuses.addAll(statuses)
        fixture.api.challengeValue = Base64.getUrlEncoder().withoutPadding().encodeToString(byteArrayOf(1, 2))
        fixture.repository = fixture.newRepository()
        return fixture
    }

    private fun repository(
        store: IdentityStore,
        keys: DeviceKeyOperations,
        api: DeviceAuthService,
        alias: String,
    ) = PairingRepository(
        identityStore = store,
        keyOps = keys,
        apiFactory = DeviceAuthApiFactory { api },
        nowEpochMillis = { 1_000L },
        pollDelay = {},
        aliasFactory = { alias },
    )

    private fun replacementIdentity() = DeviceIdentity(
        serverOrigin = "https://replacement.example",
        deviceId = 999,
        deviceName = "Replacement",
        keyAlias = "replacement-key",
        pending = null,
    )

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
        @Volatile
        var value: DeviceIdentity? = null
        val saved = mutableListOf<DeviceIdentity>()
        var clearError: Throwable? = null

        override fun read(): DeviceIdentity? = value
        override fun save(identity: DeviceIdentity) {
            value = identity
            saved += identity
        }
        override fun clear() {
            value = null
            clearError?.let { throw it }
        }
    }

    private class BlockingFirstSaveIdentityStore : IdentityStore {
        @Volatile
        private var value: DeviceIdentity? = null
        val firstSaveEntered = CountDownLatch(1)
        val releaseFirstSave = CountDownLatch(1)
        private val saveCalls = AtomicInteger()

        override fun read(): DeviceIdentity? = value

        override fun save(identity: DeviceIdentity) {
            if (saveCalls.incrementAndGet() == 1) {
                firstSaveEntered.countDown()
                check(releaseFirstSave.await(2, TimeUnit.SECONDS)) { "Timed out releasing first identity save" }
            }
            value = identity
        }

        override fun clear() {
            value = null
        }
    }

    private class FakeKeys(private val signature: ByteArray) : DeviceKeyOperations {
        val aliases = mutableSetOf<String>()
        val deleted = mutableListOf<String>()
        var lastChallenge: ByteArray? = null
        var deleteError: Throwable? = null

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
            deleteError?.let { throw it }
            deleted += alias
            aliases -= alias
        }
    }

    private class RacingKeys : DeviceKeyOperations {
        val aliases = java.util.Collections.synchronizedSet(mutableSetOf<String>())
        val deleted = java.util.Collections.synchronizedList(mutableListOf<String>())
        val containsCalls = AtomicInteger()
        val secondContainsEntered = CountDownLatch(1)
        val firstCreateEntered = CountDownLatch(1)
        val releaseFirstCreate = CountDownLatch(1)
        private val createCalls = AtomicInteger()

        override fun contains(alias: String): Boolean {
            if (containsCalls.incrementAndGet() == 2) secondContainsEntered.countDown()
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
        var challengeCalls = 0
        var verifyCalls = 0
        var verifyError: Throwable? = null
        var verifyEntered: CountDownLatch? = null
        var releaseVerify: CountDownLatch? = null

        override suspend fun requestPairing(token: String, name: String, jwk: PublicJwk): PairClaim {
            requestError?.let { throw it }
            return claim
        }
        override suspend fun pairingStatus(pairingId: Long, claimSecret: String): PairingStatus {
            statusCalls += 1
            statusError?.let { throw it }
            return statuses.removeFirst()
        }
        override suspend fun challenge(deviceId: Long): String {
            challengeCalls += 1
            return challengeValue
        }
        override suspend fun verify(deviceId: Long, challenge: String, signature: ByteArray): AuthSession {
            verifyCalls += 1
            verifiedSignature = signature
            verifyEntered?.countDown()
            releaseVerify?.let {
                check(it.await(2, TimeUnit.SECONDS)) { "Timed out releasing verification" }
            }
            verifyError?.let { throw it }
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

    private fun assertThrowsIdentityCleanup(action: () -> Unit): IdentityCleanupFailed =
        org.junit.Assert.assertThrows(IdentityCleanupFailed::class.java, action)

    private fun takeRequest(): RecordedRequest =
        server.takeRequest(2, TimeUnit.SECONDS) ?: throw AssertionError("Expected HTTP request")
}
