import requests
import random
import time

BASE_URL = "http://localhost:5002"
SESSION = requests.Session()

def test_calendar_flow():
    print("--- Testing Care Calendar ---")
    
    # 1. Login/Signup
    username = f"cal_user_{random.randint(1000, 9999)}"
    password = "password"
    print(f"[Init] Registering {username}...")
    SESSION.post(f"{BASE_URL}/api/signup", json={"username": username, "password": password})
    SESSION.post(f"{BASE_URL}/api/login", json={"username": username, "password": password})

    # 2. Trigger Analysis (Should auto-log)
    print("\n[Step 1] Triggering Analysis (Auto-Log)...")
    resp = SESSION.post(f"{BASE_URL}/api/analyze", json={"type": "audio"})
    if resp.status_code == 200:
        print("✅ Analysis successful.")
    else:
        print(f"❌ Analysis failed: {resp.text}")
        return

    # 3. Check Calendar for Auto-Logged Event
    print("\n[Step 2] Checking Calendar for Event...")
    resp = SESSION.get(f"{BASE_URL}/api/calendar")
    events = resp.json()
    if len(events) > 0 and events[0]['event_type'] == "Analysis":
        print(f"✅ Found auto-logged event: {events[0]['cause']}")
        event_id = events[0]['id']
    else:
        print(f"❌ No event found or incorrect type. Events: {events}")
        return

    # 4. Manual Add
    print("\n[Step 3] Adding Manual Event...")
    new_event = {
        "event_type": "Feeding",
        "cause": "Formula",
        "notes": "4oz"
    }
    resp = SESSION.post(f"{BASE_URL}/api/calendar", json=new_event)
    if resp.status_code == 201:
        manual_id = resp.json()['id']
        print("✅ Manual event added.")
    else:
        print(f"❌ Failed to add manual event: {resp.text}")

    # 5. Edit Event
    print("\n[Step 4] Editing Event...")
    resp = SESSION.put(f"{BASE_URL}/api/calendar/{manual_id}", json={"notes": "6oz were eaten"})
    if resp.status_code == 200:
        print("✅ Edit successful.")
    else:
        print(f"❌ Edit failed: {resp.text}")

    # 6. Verify Edit
    resp = SESSION.get(f"{BASE_URL}/api/calendar")
    latest = next(e for e in resp.json() if e['id'] == manual_id)
    if latest['notes'] == "6oz were eaten":
        print("✅ Edit Verified.")
    else:
        print(f"❌ Edit verification failed: {latest['notes']}")

    # 7. Delete Event
    print("\n[Step 5] Deleting Event...")
    resp = SESSION.delete(f"{BASE_URL}/api/calendar/{manual_id}")
    if resp.status_code == 200:
        print("✅ Delete successful.")
    else:
        print(f"❌ Delete failed: {resp.text}")

    # Final Count
    resp = SESSION.get(f"{BASE_URL}/api/calendar")
    print(f"\nRemaining Events: {len(resp.json())} (Should be >= 1 from analysis)")

if __name__ == "__main__":
    try:
        test_calendar_flow()
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to server. Is it running?")
    except Exception as e:
        print(f"❌ Error: {e}")
