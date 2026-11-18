package me.nagaev.veles.sharing

import me.nagaev.veles.R

/**
 * The status lines the sharing card shows below its title, in order, as string resource ids.
 *
 * Sharing enabled with availability [SharingAvailability.Disabled] means the service was turned on and is
 * no longer running (for example after a package replacement or a restart), so it gets one plain line
 * instead of the "does not share OTPs yet" availability line, which reads like a capability refusal.
 * Every other combination keeps the lines the card has always shown.
 */
internal fun sharingCardLines(enabled: Boolean, availability: SharingAvailability): List<Int> {
    if (enabled && availability == SharingAvailability.Disabled) return listOf(R.string.sharing_stopped)
    val own = when (availability) {
        SharingAvailability.Disabled -> R.string.sharing_disabled
        SharingAvailability.Starting -> R.string.sharing_starting
        SharingAvailability.Advertising -> R.string.sharing_advertising
        SharingAvailability.Unsupported -> R.string.sharing_unsupported
        SharingAvailability.PermissionRequired -> R.string.sharing_permission
        SharingAvailability.BluetoothOff -> R.string.sharing_bluetooth_off
        SharingAvailability.Failed -> R.string.sharing_failed
    }
    val active = availability == SharingAvailability.Starting || availability == SharingAvailability.Advertising
    return if (enabled && !active) listOf(R.string.sharing_enabled_unavailable, own) else listOf(own)
}
