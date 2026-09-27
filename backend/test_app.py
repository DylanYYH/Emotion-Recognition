import requests
import json

def test_analyze():
    url = "http://127.0.0.1:5001/api/analyze"
    headers = {'Content-Type': 'application/json'}
    data = {'type': 'audio'} # Mock payload
    
    try:
        response = requests.post(url, headers=headers, json=data)
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(json.dumps(response.json(), indent=2))
        
        if response.status_code == 200:
            print("\n✅ Test Passed: API is reachable and returning data.")
        else:
            print("\n❌ Test Failed: Non-200 status code.")
            
    except Exception as e:
        print(f"\n❌ Test Failed: Could not connect to server. {e}")

if __name__ == "__main__":
    print("Testing /api/analyze endpoint...")
    test_analyze()
