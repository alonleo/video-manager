package com.framearchive.mobile.web

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.DownloadListener
import android.webkit.PermissionRequest
import android.webkit.SafeBrowsingResponse
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebStorage
import android.net.http.SslError
import androidx.annotation.RequiresApi
import java.io.File
import java.nio.file.Files
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient

class WebSessionBridge(
    private val context: Context,
    private val onBlockedNavigation: () -> Unit,
    private val onExternalNavigationUnavailable: () -> Unit,
    private val onUnauthorized: () -> Unit,
    private val onSessionError: () -> Unit,
    private val onDownloadQueued: () -> Unit,
    private val onDownloadCompleted: () -> Unit,
    private val onDownloadFailed: () -> Unit,
) {
    private val dataOperations = SHARED_DATA_OPERATIONS
    private val downloadClient = OkHttpClient()

    fun open(webView: WebView, serverOrigin: String, setCookieHeader: String): SessionOpen {
        val policy = TrustedOriginPolicy(serverOrigin)
        val cookie = SessionCookiePolicy.plan(policy.normalizedOrigin, setCookieHeader)
            ?: throw IllegalArgumentException("Authentication response contains an invalid session cookie")
        val operation = SessionOpen()
        val token = dataOperations.open { activeToken ->
            val cookieManager = CookieManager.getInstance()
            val expired = "frame_device_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"

            // CookieManager is asynchronous. Keep this chain in the security-sensitive order:
            // expire this site's old session, accept cookies, write one validated session, persist,
            // configure the WebView, then navigate. The generation token rejects stale callbacks.
            cookieManager.setCookie(cookie.cookieUrl, expired) {
                if (!activeToken.isCurrent() || operation.isCancelled) return@setCookie
                cookieManager.setAcceptCookie(true)
                cookieManager.setCookie(cookie.cookieUrl, cookie.cookieValue) { accepted ->
                    if (!activeToken.isCurrent() || operation.isCancelled) return@setCookie
                    if (!accepted) {
                        onSessionError()
                        return@setCookie
                    }
                    cookieManager.flush()
                    configure(webView, policy, cookie.cookieValue, operation)
                    if (activeToken.isCurrent() && !operation.isCancelled) webView.loadUrl(cookie.cookieUrl)
                }
            }
        }
        operation.onCancel(token::cancel)
        return operation
    }

    fun clearAllWebData(webView: WebView?) {
        dataOperations.clear { complete ->
            try {
                WebStorage.getInstance().deleteAllData()
            } catch (_: RuntimeException) {
                // Cookie removal remains mandatory even if a WebView provider cannot clear storage.
            }
            val scratch = if (webView == null) {
                try {
                    WebView(context)
                } catch (_: RuntimeException) {
                    null
                }
            } else {
                null
            }
            try {
                (webView ?: scratch)?.clearCache(true)
            } catch (_: RuntimeException) {
                // Continue to the global cookie clear below.
            }
            try {
                CookieManager.getInstance().removeAllCookies {
                    try {
                        CookieManager.getInstance().flush()
                        scratch?.destroy()
                    } finally {
                        complete()
                    }
                }
            } catch (_: RuntimeException) {
                try {
                    scratch?.destroy()
                } finally {
                    complete()
                }
            }
        }
    }

    private fun configure(
        webView: WebView,
        policy: TrustedOriginPolicy,
        normalizedCookie: String,
        operation: SessionOpen,
    ) {
        WebView.setWebContentsDebuggingEnabled(false)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            setGeolocationEnabled(false)
            safeBrowsingEnabled = true
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                request.deny()
            }

            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams,
            ): Boolean {
                filePathCallback.onReceiveValue(null)
                return true
            }

            override fun onGeolocationPermissionsShowPrompt(
                origin: String,
                callback: android.webkit.GeolocationPermissions.Callback,
            ) {
                callback.invoke(origin, false, false)
            }
        }
        webView.webViewClient = RestrictedWebViewClient(policy)
        webView.setDownloadListener(
            SameOriginDownloadListener(policy, normalizedCookie, operation),
        )
    }

    private inner class RestrictedWebViewClient(
        private val policy: TrustedOriginPolicy,
    ) : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
            handleNavigation(request.url.toString(), request.isForMainFrame)

        @Deprecated("Only called on old platform versions")
        override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean =
            handleNavigation(url, true)

        private fun handleNavigation(url: String, isMainFrame: Boolean): Boolean = when (policy.decide(url)) {
            NavigationDecision.SAME_ORIGIN -> false
            NavigationDecision.EXTERNAL_HTTPS -> {
                if (isMainFrame) openExternalHttps(url)
                true
            }
            NavigationDecision.BLOCK -> {
                onBlockedNavigation()
                true
            }
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            handler.cancel()
        }

        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            if (request.isForMainFrame && errorResponse.statusCode in setOf(401, 403)) {
                onUnauthorized()
            }
        }

        @RequiresApi(Build.VERSION_CODES.O_MR1)
        override fun onSafeBrowsingHit(
            view: WebView,
            request: WebResourceRequest,
            threatType: Int,
            callback: SafeBrowsingResponse,
        ) {
            callback.backToSafety(true)
            onBlockedNavigation()
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError,
        ) {
            if (request.isForMainFrame) onSessionError()
        }
    }

    private inner class SameOriginDownloadListener(
        private val policy: TrustedOriginPolicy,
        normalizedCookie: String,
        private val operation: SessionOpen,
    ) : DownloadListener {
        private val cookiePair = normalizedCookie.substringBefore(';')

        override fun onDownloadStart(
            url: String,
            userAgent: String?,
            contentDisposition: String?,
            mimeType: String?,
            contentLength: Long,
        ) {
            if (policy.decide(url) != NavigationDecision.SAME_ORIGIN) {
                onBlockedNavigation()
                return
            }
            onDownloadQueued()
            operation.launchDownload {
                val directory = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                    ?: File(context.filesDir, "downloads")
                if (!directory.exists() && !directory.mkdirs()) {
                    notifyDownloadFailure(operation)
                    return@launchDownload
                }
                val temporary = try {
                    File.createTempFile(".frame-download-", ".part", directory)
                } catch (_: Exception) {
                    notifyDownloadFailure(operation)
                    return@launchDownload
                }
                val fetcher = SameOriginDownloadFetcher(downloadClient, policy, cookiePair)
                val unregister = operation.registerCancellation(fetcher::cancel)
                try {
                    val result = temporary.outputStream().buffered().use { output ->
                        fetcher.fetch(url, output)
                    }
                    if (result is DownloadFetchResult.Success && !operation.isCancelled) {
                        val destination = uniqueDestination(directory, result.filename)
                        try {
                            Files.move(temporary.toPath(), destination.toPath())
                            withContext(Dispatchers.Main.immediate) {
                                if (!operation.isCancelled) onDownloadCompleted()
                            }
                        } catch (_: Exception) {
                            notifyDownloadFailure(operation)
                        }
                    } else {
                        notifyDownloadFailure(operation)
                    }
                } finally {
                    unregister()
                    if (temporary.exists()) temporary.delete()
                }
            }
        }
    }

    private fun openExternalHttps(url: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
            addCategory(Intent.CATEGORY_BROWSABLE)
        }
        try {
            context.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            onExternalNavigationUnavailable()
        } catch (_: SecurityException) {
            onExternalNavigationUnavailable()
        } catch (_: RuntimeException) {
            onExternalNavigationUnavailable()
        }
    }

    private suspend fun notifyDownloadFailure(operation: SessionOpen) {
        withContext(Dispatchers.Main.immediate) {
            if (!operation.isCancelled) onDownloadFailed()
        }
    }

    private fun uniqueDestination(directory: File, filename: String): File {
        val safe = DownloadFilenamePolicy.sanitize(filename)
        var candidate = File(directory, safe)
        var counter = 1
        while (candidate.exists()) {
            val dot = safe.lastIndexOf('.').takeIf { it > 0 } ?: safe.length
            val stem = safe.substring(0, dot)
            val extension = safe.substring(dot)
            candidate = File(directory, "$stem ($counter)$extension")
            counter += 1
        }
        return candidate
    }

    private companion object {
        // CookieManager and WebStorage are process-global. Sharing the gate across Activity
        // recreation prevents an old cleanup callback from racing a new Activity's session open.
        val SHARED_DATA_OPERATIONS = WebDataOperationCoordinator()
    }
}

class SessionOpen internal constructor() {
    private val cancelled = AtomicBoolean(false)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val cancellationActions = CopyOnWriteArrayList<() -> Unit>()
    internal val isCancelled: Boolean get() = cancelled.get()

    internal fun onCancel(action: () -> Unit) {
        if (isCancelled) {
            action()
            return
        }
        cancellationActions += action
        if (isCancelled && cancellationActions.remove(action)) action()
    }

    internal fun registerCancellation(action: () -> Unit): () -> Unit {
        onCancel(action)
        return { cancellationActions.remove(action) }
    }

    internal fun launchDownload(block: suspend CoroutineScope.() -> Unit) {
        if (!isCancelled) scope.launch(block = block)
    }

    fun cancel() {
        if (!cancelled.compareAndSet(false, true)) return
        cancellationActions.forEach { action -> action() }
        cancellationActions.clear()
        scope.cancel()
    }
}
