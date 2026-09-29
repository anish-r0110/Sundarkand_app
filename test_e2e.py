import urllib.request
import urllib.parse
import http.cookiejar
import json
import sys

base_url = 'http://127.0.0.1:8000'

def make_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    return opener

def req(opener, method, path, data=None):
    url = base_url + path
    headers = {'Content-Type': 'application/json'}
    body = json.dumps(data).encode('utf-8') if data is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body

admin_ses = make_session()
member_ses = make_session()

print("=== 1. Test Static Files & Manifest ===")
with urllib.request.urlopen(base_url + '/') as r:
    assert r.status == 200
    content = r.read().decode('utf-8')
    assert "Sundarkand Seva" in content
    print("[OK] Static index.html served correctly")

with urllib.request.urlopen(base_url + '/static/manifest.webmanifest') as r:
    assert r.status == 200
    manifest = json.loads(r.read().decode('utf-8'))
    assert manifest["short_name"] == "Sundarkand"
    print("[OK] PWA Manifest verified:", manifest["name"])

with urllib.request.urlopen(base_url + '/static/sw.js') as r:
    assert r.status == 200
    print("[OK] Service worker script served correctly")

print("\n=== 2. Test Auth & Invitations ===")
# Test uninvited email
status, res = req(admin_ses, 'POST', '/api/auth/status-for-email', {'email': 'stranger@example.com'})
assert res['invited'] == False
print("[OK] Uninvited email rejected correctly:", res)

# Check admin email
status, res = req(admin_ses, 'POST', '/api/auth/status-for-email', {'email': 'admin@sundarkand.org'})
assert res['invited'] == True
print("[OK] Admin email pre-invite confirmed:", res)

# If admin already registered from earlier test, log in; otherwise register
if res['registered']:
    status, res = req(admin_ses, 'POST', '/api/auth/login', {
        'email': 'admin@sundarkand.org',
        'password': 'adminpassword123'
    })
    assert status == 200
    print("[OK] Admin logged in successfully:", res['user']['display_name'])
else:
    status, res = req(admin_ses, 'POST', '/api/auth/register', {
        'email': 'admin@sundarkand.org',
        'display_name': 'Ramesh Bhai (Admin)',
        'password': 'adminpassword123'
    })
    assert status == 200
    print("[OK] Admin registered successfully:", res['user']['display_name'])

# Check admin session
status, res = req(admin_ses, 'GET', '/api/auth/check')
assert res['user']['email'] == 'admin@sundarkand.org'
assert res['user']['role'] == 'admin'
print("[OK] Admin session active:", res['user'])

# Invite a new member
test_member_email = 'shyam@sundarkand.org'
req(admin_ses, 'POST', '/api/admin/invites', {'email': test_member_email})
print("[OK] Admin invite ensured for:", test_member_email)

# Check member status
_, mem_stat = req(member_ses, 'POST', '/api/auth/status-for-email', {'email': test_member_email})
if mem_stat['registered']:
    status, res = req(member_ses, 'POST', '/api/auth/login', {
        'email': test_member_email,
        'password': 'memberpass123'
    })
    assert status == 200
    print("[OK] Member logged in:", res['user']['display_name'])
else:
    status, res = req(member_ses, 'POST', '/api/auth/register', {
        'email': test_member_email,
        'display_name': 'Shyam Sundar',
        'password': 'memberpass123'
    })
    assert status == 200
    print("[OK] Member registered:", res['user']['display_name'], "Role:", res['user']['role'])

print("\n=== 3. Test Events & RSVP ===")
# Admin creates event
status, res = req(admin_ses, 'POST', '/api/events', {
    'title': 'Diwali Deepotsav Sundarkand',
    'event_date': '2026-11-01',
    'start_time': '18:00',
    'venue': 'Hanuman Temple Courtyard',
    'address': 'Main Temple Road, Ayodhya Enclave',
    'notes': 'Dress code: Traditional saffron. Team brings musical instruments.'
})
assert status == 200
event_id = res['id']
print("[OK] Event created with ID:", event_id)

# Member views events
status, res = req(member_ses, 'GET', '/api/events')
assert status == 200
assert len(res['upcoming']) > 0
print("[OK] Member fetched upcoming events:", len(res['upcoming']))

# Member RSVPs 'going'
status, res = req(member_ses, 'POST', f'/api/events/{event_id}/rsvp', {'status': 'going'})
assert res['status'] == 'going'
print("[OK] Member RSVP 'going' recorded")

# Admin RSVPs 'maybe'
status, res = req(admin_ses, 'POST', f'/api/events/{event_id}/rsvp', {'status': 'maybe'})
assert res['status'] == 'maybe'
print("[OK] Admin RSVP 'maybe' recorded")

print("\n=== 4. Test Attendance (Public Roster Visibility) ===")
# Member view attendance (roster is now visible to everyone)
status, res = req(member_ses, 'GET', f'/api/attendance/{event_id}')
assert 'roster' in res
assert res['counts']['going'] >= 1
assert res['counts']['maybe'] >= 1
print("[OK] Member can see attendance counts and roster of who is going/not going:", res['counts'])
print("  Member sees going:", [m['display_name'] for m in res['roster']['going']])

# Admin view attendance (full roster with names)
status, res = req(admin_ses, 'GET', f'/api/attendance/{event_id}')
assert 'roster' in res
assert len(res['roster']['going']) >= 1
print("[OK] Admin sees full roster with member names!")
print("  Going members:", [m['display_name'] for m in res['roster']['going']])
print("  Maybe members:", [m['display_name'] for m in res['roster']['maybe']])

print("\n=== 5. Test Realtime Chat API ===")
# Member posts chat
status, res = req(member_ses, 'POST', '/api/chat', {'content': 'Jai Bajrangbali! Har Har Mahadev.'})
assert status == 200
chat_id = res['message']['id']
print("[OK] Member posted chat message:", chat_id, res['message']['content'])

# Admin fetches chat
status, res = req(admin_ses, 'GET', '/api/chat')
assert any(m['id'] == chat_id for m in res['messages'])
print("[OK] Admin sees message in chat stream")

# Member deletes own message
status, res = req(member_ses, 'DELETE', f'/api/chat/{chat_id}')
assert res['success'] == True
print("[OK] Member deleted own message successfully")

print("\n=== 6. Test Profile, Push & Admin Management ===")
# Member updates display name
status, res = req(member_ses, 'PUT', '/api/profile/name', {'display_name': 'Shyam Sundar (Harmonium)'})
assert res['display_name'] == 'Shyam Sundar (Harmonium)'
print("[OK] Member updated display name")

# Member updates notification preferences
status, res = req(member_ses, 'PUT', '/api/profile/notifications', {
    'new_events': True,
    'event_changes': True,
    'reminders': True,
    'chat': False
})
assert res['success'] == True
print("[OK] Notification preferences updated")

# Get VAPID public key
status, res = req(member_ses, 'GET', '/api/push/vapid-public-key')
assert len(res['public_key']) > 20
print("[OK] VAPID public key endpoint returned key")

# Test push dispatch route
status, res = req(member_ses, 'POST', '/api/push/test')
assert res['success'] == True
print("[OK] Push test endpoint queued successfully")

# Admin lists members
status, res = req(admin_ses, 'GET', '/api/admin/members')
assert len(res['members']) >= 2
print("[OK] Admin listed all team members:", [m['display_name'] for m in res['members']])

print("\n===========================================")
print(" ALL TESTS PASSED SUCCESSFULLY! ")
print("===========================================")
