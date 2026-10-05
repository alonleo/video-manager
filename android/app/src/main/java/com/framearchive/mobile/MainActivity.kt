package com.framearchive.mobile

import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.framearchive.mobile.databinding.ActivityMainBinding
import com.framearchive.mobile.databinding.ViewApprovalBinding
import com.framearchive.mobile.databinding.ViewPairingBinding
import com.framearchive.mobile.pairing.PairingRepository
import com.framearchive.mobile.storage.EncryptedIdentityStore
import com.framearchive.mobile.ui.AppState
import com.framearchive.mobile.ui.MainUiState
import com.framearchive.mobile.ui.MainViewModel
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
        binding.content.removeAllViews()
        when (state) {
            AppState.NeedsPairing -> renderPairing()
            AppState.Submitting -> renderProgress(getString(R.string.pairing_submitting))
            is AppState.AwaitingApproval -> renderApproval(state)
            AppState.Authenticating -> renderProgress(getString(R.string.pairing_authenticating))
            is AppState.Browsing -> renderStatus(getString(R.string.browser_placeholder))
            is AppState.RecoverableError -> {
                if (state.canRetry) {
                    renderRetryableError(state.message, uiState.canResetIdentity)
                } else {
                    renderPairing(state.message)
                }
            }
        }
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
        binding.content.addView(pairing.root)
    }

    private fun renderApproval(state: AppState.AwaitingApproval) {
        val approval = ViewApprovalBinding.inflate(layoutInflater, binding.content, false)
        val details = viewModel.approvalDetails.value
        approval.verificationCode.text = state.code.chunked(3).joinToString(" ")
        approval.serverHost.text = details?.serverHost ?: getString(R.string.unknown_server)
        approval.deviceName.text = details?.deviceName ?: viewModel.pairingDraft.value.deviceName
        approval.cancelButton.setOnClickListener { viewModel.cancelPairing() }
        binding.content.addView(approval.root)
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
        val container = statusContainer()
        container.addView(ProgressBar(this))
        container.addView(statusText(message))
        binding.content.addView(container)
    }

    private fun renderStatus(message: String) {
        binding.content.addView(statusContainer().apply { addView(statusText(message)) })
    }

    private fun renderRetryableError(message: String, canResetIdentity: Boolean) {
        val container = statusContainer()
        container.addView(statusText(message))
        container.addView(Button(this).apply {
            setText(R.string.retry)
            setOnClickListener { viewModel.retry() }
        })
        if (canResetIdentity) {
            container.addView(Button(this).apply {
                setText(R.string.pair_again)
                setOnClickListener { viewModel.abandonIdentity() }
            })
        }
        binding.content.addView(container)
    }

    private fun statusContainer() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        val padding = resources.getDimensionPixelSize(R.dimen.screen_padding)
        setPadding(padding, padding, padding, padding)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.MATCH_PARENT,
        )
    }

    private fun statusText(message: String) = TextView(this).apply {
        text = message
        gravity = Gravity.CENTER
        textSize = 18f
        accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        setPadding(0, resources.getDimensionPixelSize(R.dimen.item_spacing), 0, 0)
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

    private companion object {
        fun defaultDeviceName(): String =
            listOf(Build.MANUFACTURER, Build.MODEL)
                .filter { it.isNotBlank() }
                .joinToString(" ")
                .ifBlank { "Android" }
    }
}
