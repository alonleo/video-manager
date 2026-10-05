package com.framearchive.mobile.web

import android.app.DownloadManager
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
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.net.http.SslError
import androidx.annotation.RequiresApi
import java.util.concurrent.atomic.AtomicBoolean

class WebSessionBridge(
    private val context: Context,
    private val onBlockedNavigation: () -> Unit,
    private val onExternalNavigationUnavailable: () -> Unit,
    private val onUnauthorized: () -> Unit,
    private val onSessionError: () -> Unit,
    private val onDownloadQueued: () -> Unit,
) {
    fun open(webView: WebView, serverOrigin: String, setCookieHeader: String): SessionOpen {
        val policy = TrustedOriginPolicy(serverOrigin)
        val cookie = SessionCookiePolicy.plan(policy.normalizedOrigin, setCookieHeader)
            ?: throw IllegalArgumentException("Authentication response contains an invalid session cookie")
        val operation = SessionOpen()
        val cookieManager = CookieManager.getInstance()
        val expired = "frame_device_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"

        // CookieManager is asynchronous. Keep this chain in the security-sensitive order documented
        // by the design: expire this site's old session, accept cookies, write one validated session,
        // persist it, configure the WebView, then navigate.
        cookieManager.setCookie(cookie.cookieUrl, expired) {
            if (operation.isCancelled) return@setCookie
            cookieManager.setAcceptCookie(true)
            cookieManager.setCookie(cookie.cookieUrl, cookie.cookieValue) { accepted ->
                if (operation.isCancelled) return@setCookie
                if (!accepted) {
                    onSessionError()
                    return@setCookie
                }
                cookieManager.flush()
                configure(webView, policy, cookie.cookieValue)
                if (!operation.isCancelled) webView.loadUrl(cookie.cookieUrl)
            }
        }
        return operation
    }

    fun clearSession(serverOrigin: String) {
        val cookieUrl = try {
            "${TrustedOriginPolicy(serverOrigin).normalizedOrigin}/"
        } catch (_: IllegalArgumentException) {
            return
        }
        CookieManager.getInstance().apply {
            setCookie(
                cookieUrl,
                "frame_device_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax",
            ) { flush() }
        }
    }

    private fun configure(webView: WebView, policy: TrustedOriginPolicy, normalizedCookie: String) {
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
            SameOriginDownloadListener(policy, normalizedCookie, webView.settings.userAgentString),
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
        private val userAgent: String,
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
            val uri = try {
                Uri.parse(url)
            } catch (_: RuntimeException) {
                onBlockedNavigation()
                return
            }
            val filename = safeFilename(URLUtil.guessFileName(url, contentDisposition, mimeType))
            val request = try {
                DownloadManager.Request(uri)
                    .setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED,
                    )
                    .setTitle(filename)
                    .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, filename)
                    .addRequestHeader("Cookie", cookiePair)
                    .addRequestHeader("User-Agent", userAgent ?: this.userAgent)
            } catch (_: RuntimeException) {
                onBlockedNavigation()
                return
            }
            val manager = context.getSystemService(DownloadManager::class.java)
            try {
                manager.enqueue(request)
                onDownloadQueued()
            } catch (_: RuntimeException) {
                onSessionError()
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

    private fun safeFilename(candidate: String): String {
        val cleaned = candidate
            .replace(Regex("[\\u0000-\\u001f\\u007f/\\\\:]"), "_")
            .trim()
            .take(120)
        return cleaned.ifBlank { "download" }
    }
}

class SessionOpen internal constructor() {
    private val cancelled = AtomicBoolean(false)
    internal val isCancelled: Boolean get() = cancelled.get()

    fun cancel() {
        cancelled.set(true)
    }
}
