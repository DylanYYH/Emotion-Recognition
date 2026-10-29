import requests
import random

BASE_URL = "http://localhost:5002"
SESSION = requests.Session()

def test_auto_monitor_route():
    print("--- Testing Auto Monitor Component ---")
    
    # Login Flow
    username = f"user_{random.randint(10000, 99999)}"
    password = "testpassword"
    
    print(f"Registering {username}...")
    resp = SESSION.post(f"{BASE_URL}/api/signup", json={"username": username, "password": password})
    if resp.status_code != 200:
        print("Registration failed")
        return

    print("Logging in...")
    resp = SESSION.post(f"{BASE_URL}/api/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        print("Login failed")
        return

    # Check Route
    print("Checking /auto-monitor route...")
    resp = SESSION.get(f"{BASE_URL}/auto-monitor")
    if resp.status_code == 200:
        print("✅ /auto-monitor route is accessible (200 OK)")
        if "FirstVoice - Auto Monitor" in resp.text:
             print("✅ Content appears correct (Title match)")
        else:
             print("⚠️  Content might be unexpected")
    else:
        print(f"❌ Failed to access /auto-monitor: {resp.status_code}")

if __name__ == "__main__":
    try:
        test_auto_monitor_route()
    except Exception as e:
        print(f"Error: {e}")
