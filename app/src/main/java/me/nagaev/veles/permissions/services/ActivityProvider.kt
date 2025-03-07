package me.nagaev.veles.permissions.services

import androidx.activity.ComponentActivity

interface ActivityProvider {
    fun getActivity(): ComponentActivity
}

class ActivityProviderImpl(
    private val activity: ComponentActivity
) : ActivityProvider {

    override fun getActivity(): ComponentActivity {
        return activity
    }
}

