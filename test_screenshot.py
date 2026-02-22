import requests
import json
import time
import sys

def test_screenshot():
    url = "http://localhost:3001/screenshot"
    payload = {"url": "http://example.com"}
    headers = {"Content-Type": "application/json"}

    print(f"Testing screenshot service at {url}...")
    
    # Retry logic for server startup
    for i in range(5):
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if "screenshot" in data and data["screenshot"].startswith("data:image/png;base64,"):
                    print("SUCCESS: Screenshot captured and returned valid base64.")
                    return True
                else:
                    print(f"FAILURE: Invalid response format: {data.keys()}")
                    return False
            else:
                print(f"FAILURE: Status code {response.status_code}")
                print(response.text)
                return False
        except requests.exceptions.ConnectionError:
            print(f"Server not ready, retrying ({i+1}/5)...")
            time.sleep(2)
            
    print("FAILURE: meaningful connection could not be established.")
    return False

if __name__ == "__main__":
    if test_screenshot():
        sys.exit(0)
    else:
        sys.exit(1)
