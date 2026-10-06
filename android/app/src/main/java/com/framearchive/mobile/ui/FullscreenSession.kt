package com.framearchive.mobile.ui

class FullscreenSession<View, Callback>(
    private val notifyHidden: (Callback) -> Unit,
) {
    var currentView: View? = null
        private set
    private var currentCallback: Callback? = null

    fun show(view: View, callback: Callback): Boolean {
        if (currentView != null) {
            notifyHidden(callback)
            return false
        }
        currentView = view
        currentCallback = callback
        return true
    }

    fun hide(): View? {
        val view = currentView ?: return null
        val callback = currentCallback
        currentView = null
        currentCallback = null
        if (callback != null) notifyHidden(callback)
        return view
    }
}
