import requests
import json

BASE_URL = "http://localhost:5002"
SESSION = requests.Session()

def test_features():
    print("--- Testing New Features (Backend) ---")
    
    # 1. Register
    print("[1] Registering user...")
    username = "feature_test_user"
    password = "password123"
    import random
    username = f"user_{random.randint(10000, 99999)}"
    
    response = SESSION.post(f"{BASE_URL}/api/signup", json={"username": username, "password": password})
    if response.status_code != 200:
        print(f"❌ Failed to register: {response.text}")
        return

    # 2. Login
    print("[2] Logging in...")
    response = SESSION.post(f"{BASE_URL}/api/login", json={"username": username, "password": password})
    if response.status_code != 200:
        print(f"❌ Failed to login: {response.text}")
        return

    # 3. Check Default Settings
    print("[3] Checking default settings...")
    response = SESSION.get(f"{BASE_URL}/api/user")
    if response.status_code == 200:
        data = response.json()
        if data['auto_listen_enabled'] == False:
            print("✅ Default setting correct (False).")
        else:
            print(f"❌ Unexpected default: {data['auto_listen_enabled']}")
    else:
        print(f"❌ Failed to get user: {response.text}")

    # 4. Update Settings (Enable Auto-Listen)
    print("\n[4] Enabling Auto-Listen...")
    response = SESSION.post(f"{BASE_URL}/api/settings", json={"auto_listen": True})
    if response.status_code == 200:
        print("✅ Settings updated.")
    else:
        print(f"❌ Failed update: {response.text}")

    # 5. Verify Persistence
    print("[5] Verifying persistence...")
    response = SESSION.get(f"{BASE_URL}/api/user")
    if response.status_code == 200:
        data = response.json()
        if data['auto_listen_enabled'] == True:
            print("✅ Persistence verified (True).")
        else:
            print(f"❌ Persistence failed: {data['auto_listen_enabled']}")

if __name__ == "__main__":
    try:
        test_features()
    except Exception as e:
        print(f"Error: {e}")
