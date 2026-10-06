package com.framearchive.mobile

import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.framearchive.mobile.databinding.ActivityMainBinding
import com.framearchive.mobile.databinding.ViewApprovalBinding
import com.framearchive.mobile.databinding.ViewBrowserBinding
import com.framearchive.mobile.databinding.ViewPairingBinding
import com.framearchive.mobile.databinding.ViewStatusBinding
import com.framearchive.mobile.pairing.PairingRepository
import com.framearchive.mobile.storage.EncryptedIdentityStore
import com.framearchive.mobile.ui.AppState
import com.framearchive.mobile.ui.FullscreenSession
import com.framearchive.mobile.ui.MainUiState
import com.framearchive.mobile.ui.MainViewModel
import com.framearchive.mobile.web.SessionOpen
import com.framearchive.mobile.web.WebSessionBridge
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import kotlin.math.max

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private var countdownJob: Job? = null
    private var browserBinding: ViewBrowserBinding? = null
    private var browserOpen: SessionOpen? = null
    private var normalCutoutMode: Int? = null
    private val fullscreenSession =
        FullscreenSession<View, WebChromeClient.CustomViewCallback> { it.onCustomViewHidden() }

    private val webSessionBridge: WebSessionBridge by lazy {
        WebSessionBridge(
            context = this,
            onBlockedNavigation = { toast(R.string.navigation_blocked) },
            onExternalNavigationUnavailable = { toast(R.string.external_browser_unavailable) },
            onUnauthorized = { showBrowserAuthenticationProblem(R.string.session_expired) },
            onSessionError = { showBrowserAuthenticationProblem(R.string.web_session_failed) },
            onDownloadQueued = { toast(R.string.download_queued) },
            onDownloadCompleted = { toast(R.string.download_completed) },
            onDownloadFailed = { toast(R.string.download_failed) },
            onShowFullscreen = ::showFullscreen,
            onHideFullscreen = ::hideFullscreen,
        )
    }

    private val viewModel: MainViewModel by viewModels {
        val coordinator = PairingRepository(
            EncryptedIdentityStore(applicationContext),
            OkHttpClient(),
        )
        object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                require(modelClass.isAssignableFrom(MainViewModel::class.java))
                return MainViewModel(coordinator, initialDeviceName = defaultDeviceName()) as T
            }
        }
    }

    private val scanner: ActivityResultLauncher<ScanOptions> =
        registerForActivityResult(ScanContract()) { result ->
            if (viewModel.updatePairingLinkFromScan(result.contents)) {
                viewModel.submit()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (fullscreenSession.currentView != null) {
                        hideFullscreen()
                        return
                    }
                    val webView = browserBinding?.webView
                    if (webView?.canGoBack() == true) webView.goBack() else finish()
                }
            },
        )

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.uiState.collectLatest(::render)
            }
        }
    }

    private fun render(uiState: MainUiState) {
        val state = uiState.appState
        countdownJob?.cancel()
        countdownJob = null
        if (uiState.clearWebData) {
            webSessionBridge.clearAllWebData(browserBinding?.webView)
        }
        if (state !is AppState.Browsing) {
            disposeBrowser()
        }
        binding.content.removeAllViews()
        when (state) {
            AppState.NeedsPairing -> renderPairing()
            AppState.Submitting -> renderProgress(getString(R.string.pairing_submitting))
            is AppState.AwaitingApproval -> renderApproval(state)
            AppState.Authenticating -> renderProgress(getString(R.string.pairing_authenticating))
            is AppState.Browsing -> renderBrowser(state)
            is AppState.RecoverableError -> {
                if (state.canRetry) {
                    renderRetryableError(state.message, uiState.canResetIdentity)
                } else {
                    renderPairing(state.message)
                }
            }
        }
    }

    private fun renderBrowser(state: AppState.Browsing) {
        disposeBrowser()
        val browser = ViewBrowserBinding.inflate(layoutInflater, binding.content, false)
        browser.serverOrigin.text = state.serverOrigin
        browser.reauthenticateButton.setOnClickListener { viewModel.reauthenticate() }
        showContent(browser.root, animate = false)
        browserBinding = browser
        browserOpen = try {
            webSessionBridge.open(browser.webView, state.serverOrigin, state.cookieHeader)
        } catch (_: IllegalArgumentException) {
            showBrowserAuthenticationProblem(R.string.web_session_failed)
            null
        }
    }

    private fun showBrowserAuthenticationProblem(message: Int) {
        val browser = browserBinding ?: return
        browser.browserNotice.setText(message)
        browser.browserNotice.visibility = View.VISIBLE
        browser.reauthenticateButton.visibility = View.VISIBLE
    }

    private fun disposeBrowser() {
        hideFullscreen()
        browserOpen?.cancel()
        browserOpen = null
        browserBinding?.root?.let { root ->
            (root.parent as? ViewGroup)?.removeView(root)
        }
        browserBinding?.webView?.apply {
            stopLoading()
            clearHistory()
            removeAllViews()
            destroy()
        }
        browserBinding = null
    }

    private fun showFullscreen(
        view: View,
        callback: WebChromeClient.CustomViewCallback,
    ) {
        if (!fullscreenSession.show(view, callback)) return
        allowFullscreenInDisplayCutout()
        (view.parent as? ViewGroup)?.removeView(view)
        binding.content.addView(
            view,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
        view.post(::hideSystemBars)
    }

    @Suppress("DEPRECATION")
    private fun hideSystemBars() {
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        WindowCompat.getInsetsController(window, window.decorView).apply {
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
    }

    private fun allowFullscreenInDisplayCutout() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return
        val attributes = window.attributes
        if (normalCutoutMode == null) normalCutoutMode = attributes.layoutInDisplayCutoutMode
        attributes.layoutInDisplayCutoutMode =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            } else {
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        window.attributes = attributes
    }

    private fun restoreDisplayCutoutMode() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return
        val mode = normalCutoutMode ?: return
        val attributes = window.attributes
        attributes.layoutInDisplayCutoutMode = mode
        window.attributes = attributes
        normalCutoutMode = null
    }

    private fun hideFullscreen() {
        val view = fullscreenSession.currentView ?: return
        (view.parent as? ViewGroup)?.removeView(view)
        fullscreenSession.hide()
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_VISIBLE
        WindowCompat.getInsetsController(window, window.decorView)
            .show(WindowInsetsCompat.Type.systemBars())
        restoreDisplayCutoutMode()
    }

    private fun renderPairing(errorMessage: String? = null) {
        val pairing = ViewPairingBinding.inflate(layoutInflater, binding.content, false)
        val draft = viewModel.pairingDraft.value
        pairing.deviceNameInput.setText(draft.deviceName)
        pairing.pairingLinkInput.setText(draft.pairingLink)
        pairing.errorText.apply {
            text = errorMessage.orEmpty()
            visibility = if (errorMessage.isNullOrEmpty()) View.GONE else View.VISIBLE
        }
        pairing.deviceNameInput.doAfterTextChanged {
            viewModel.updateDeviceName(it?.toString().orEmpty())
        }
        pairing.pairingLinkInput.doAfterTextChanged {
            viewModel.updatePairingLink(it?.toString().orEmpty())
        }
        pairing.scanButton.setOnClickListener { launchScanner() }
        pairing.submitButton.setOnClickListener { viewModel.submit() }
        showContent(pairing.root)
    }

    private fun renderApproval(state: AppState.AwaitingApproval) {
        val approval = ViewApprovalBinding.inflate(layoutInflater, binding.content, false)
        val details = viewModel.approvalDetails.value
        approval.verificationCode.text = state.code.chunked(3).joinToString(" ")
        approval.serverHost.text = details?.serverHost ?: getString(R.string.unknown_server)
        approval.deviceName.text = details?.deviceName ?: viewModel.pairingDraft.value.deviceName
        approval.cancelButton.setOnClickListener { viewModel.cancelPairing() }
        showContent(approval.root)
        countdownJob = lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    val seconds = max(0L, (state.expiresAt - System.currentTimeMillis() + 999L) / 1_000L)
                    approval.remainingTime.text = resources.getQuantityString(
                        R.plurals.pairing_seconds_remaining,
                        seconds.coerceAtMost(Int.MAX_VALUE.toLong()).toInt(),
                        seconds,
                    )
                    if (seconds == 0L) break
                    delay(1_000L)
                }
            }
        }
    }

    private fun renderProgress(message: String) {
        val status = ViewStatusBinding.inflate(layoutInflater, binding.content, false)
        status.statusTitle.setText(R.string.status_working)
        status.statusMessage.text = message
        showContent(status.root)
    }

    private fun renderRetryableError(message: String, canResetIdentity: Boolean) {
        val status = ViewStatusBinding.inflate(layoutInflater, binding.content, false)
        status.progressIndicator.visibility = View.GONE
        status.statusTitle.setText(R.string.status_error)
        status.statusMessage.text = message
        status.retryButton.apply {
            visibility = View.VISIBLE
            setOnClickListener { viewModel.retry() }
        }
        status.resetButton.apply {
            visibility = if (canResetIdentity) View.VISIBLE else View.GONE
            setOnClickListener { viewModel.abandonIdentity() }
        }
        showContent(status.root)
    }

    private fun showContent(view: View, animate: Boolean = true) {
        binding.content.addView(view)
        if (!animate) return
        view.alpha = 0f
        view.translationY = 10f * resources.displayMetrics.density
        view.animate()
            .alpha(1f)
            .translationY(0f)
            .setDuration(240L)
            .start()
    }

    private fun launchScanner() {
        scanner.launch(
            ScanOptions()
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt(getString(R.string.scan_prompt))
                .setBeepEnabled(false)
                .setOrientationLocked(false),
        )
    }

    override fun onResume() {
        super.onResume()
        viewModel.onHostResumed()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        val fullscreenView = fullscreenSession.currentView ?: return
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
        fullscreenView.post {
            if (fullscreenSession.currentView === fullscreenView) hideSystemBars()
        }
    }

    override fun onDestroy() {
        countdownJob?.cancel()
        disposeBrowser()
        super.onDestroy()
    }

    private fun toast(message: Int) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        fun defaultDeviceName(): String =
            listOf(Build.MANUFACTURER, Build.MODEL)
                .filter { it.isNotBlank() }
                .joinToString(" ")
                .ifBlank { "Android" }
    }
}
