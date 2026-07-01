package me.nagaev.veles.permissions.services

import android.app.Activity

interface ActivityProvider {
    fun getActivity(): Activity
}

class ActivityProviderImpl(
    private val activity: Activity,
) : ActivityProvider {
    override fun getActivity(): Activity = activity
}
