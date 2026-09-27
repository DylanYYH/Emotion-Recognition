import requests
import json

BASE_URL = "http://localhost:5002"
SESSION = requests.Session()

def test_auth_flow():
    print("--- Testing Auth Flow ---")
    
    # 1. Try to access protected route (should be redirected/unauthorized)
    # Since we return 302 redirect for browser, or 401 for API.
    # Let's hit the API endpoint
    print("[1] Accessing protected API without login...")
    response = SESSION.post(f"{BASE_URL}/api/analyze", json={"type": "audio"}, allow_redirects=False)
    if response.status_code == 401 or response.status_code == 302:
        print(f"✅ Passed: Access denied ({response.status_code}).")
    else:
        print(f"❌ Failed: Status {response.status_code}")

    # 2. Register
    print("\n[2] Registering new user...")
    username = "testuser_ver"
    password = "password123"
    payload = {"username": username, "password": password}
    
    # Clean up previous run if needed (not possible with this simple setup without DB reset, so random user)
    import random
    username = f"user_{random.randint(1000, 9999)}"
    payload["username"] = username
    
    response = SESSION.post(f"{BASE_URL}/api/signup", json=payload)
    if response.status_code == 200:
        print(f"✅ Passed: Registered {username}.")
    else:
        print(f"❌ Failed: {response.text}")

    # 3. Login
    print("\n[3] Logging in...")
    response = SESSION.post(f"{BASE_URL}/api/login", json=payload)
    if response.status_code == 200:
        print("✅ Passed: Logged in.")
    else:
        print(f"❌ Failed: {response.text}")

    # 4. Access Protected API again
    print("\n[4] Accessing protected API with login...")
    response = SESSION.post(f"{BASE_URL}/api/analyze", json={"type": "audio"})
    if response.status_code == 200:
        print("✅ Passed: Access granted.")
    else:
        print(f"❌ Failed: Status {response.status_code}")

    # 5. Logout
    print("\n[5] Logging out...")
    response = SESSION.get(f"{BASE_URL}/api/logout")
    if response.status_code == 200:
        print("✅ Passed: Logged out.")
    else:
        print(f"❌ Failed: {response.text}")

    # 6. Verify Logout (Access Denied)
    print("\n[6] Accessing protected API after logout...")
    response = SESSION.post(f"{BASE_URL}/api/analyze", json={"type": "audio"}, allow_redirects=False)
    if response.status_code == 401 or response.status_code == 302:
        print(f"✅ Passed: Access denied ({response.status_code}).")
    else:
        print(f"❌ Failed: Status {response.status_code}")

if __name__ == "__main__":
    try:
        test_auth_flow()
    except Exception as e:
        print(f"Error: {e}")
