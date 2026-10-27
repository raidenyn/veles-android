import base64, re
d = '/home/dev/.claude/jobs/52694ac4/tmp/src/'
for n in ('tag-mojom', 'tag-rtc', 'tag-port'):
    open(d + n + '.txt', 'wb').write(base64.b64decode(open(d + n + '.b64', 'rb').read()))
m = open(d + 'tag-mojom.txt').read()
print('native/messag mentions in tracked-feature mojom @155:', re.findall(r'(?i)\w*(?:native|extension|messag)\w*', m))
r = open(d + 'tag-rtc.txt').read()
print('DisableAggressiveThrottling in rtc_data_channel @155:', 'DisableAggressiveThrottling' in r)
p = open(d + 'tag-port.txt').read()
print('scheduler/throttl mentions in native_message_port.cc @155:', re.findall(r'(?i)\w*(?:schedul|throttl|freez)\w*', p))
