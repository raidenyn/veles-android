import base64, os, urllib.request
OUT = '/home/dev/repos/veles-android/.claude/worktrees/otp23-investigations/native-bridge/feasibility/evidence/investigations-2026-10-27/chromium-source/'
FILES = {
    'frame_scheduler_impl.cc': 'third_party/blink/renderer/platform/scheduler/main_thread/frame_scheduler_impl.cc',
    'web_scheduler_tracked_feature.mojom': 'third_party/blink/public/mojom/scheduler/web_scheduler_tracked_feature.mojom',
    'rtc_data_channel.cc': 'third_party/blink/renderer/modules/peerconnection/rtc_data_channel.cc',
    'native_message_port.cc': 'extensions/browser/api/messaging/native_message_port.cc',
    'scheduling_policy.h': 'third_party/blink/renderer/platform/scheduler/public/scheduling_policy.h',
}
for tag in ('155.0.8059.85', '156.0.8078.48'):
    for name, path in FILES.items():
        url = 'https://chromium.googlesource.com/chromium/src/+/refs/tags/%s/%s?format=TEXT' % (tag, path)
        try:
            data = base64.b64decode(urllib.request.urlopen(url, timeout=60).read())
            open(OUT + '%s-%s' % (tag, name), 'wb').write(data)
            print('ok', tag, name, len(data))
        except Exception as e:
            print('FAIL', tag, name, e)
# discard eligibility policy: path varies, try the known locations
for tag in ('155.0.8059.85', '156.0.8078.48'):
    for path in ('chrome/browser/resource_coordinator/discard_eligibility_policy.cc',
                 'components/performance_manager/policies/discard_eligibility_policy.cc',
                 'chrome/browser/performance_manager/policies/discard_eligibility_policy.cc'):
        url = 'https://chromium.googlesource.com/chromium/src/+/refs/tags/%s/%s?format=TEXT' % (tag, path)
        try:
            data = base64.b64decode(urllib.request.urlopen(url, timeout=60).read())
            open(OUT + '%s-discard_eligibility_policy.cc' % tag, 'wb').write(data)
            print('ok', tag, path, len(data)); break
        except Exception as e:
            print('miss', tag, path)
