import asyncio
import websockets
import json
import os

async def test_backend():
    uri = "ws://localhost:8000/ws/chat"
    
    # Mock data simulating what the extension would send
    payload = {
        "type": "universal_analyze",
        "query": "Fix this visual bug",
        # 1x1 transparent PNG - just enough to be a valid image for the vision model
        "screenshot": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "dom": {
            "url": "http://example.com",
            "selectedElement": {"tagName": "DIV"},
            "pageTitle": "Example Domain"
        },
        "repo": "test/repo",
        "network_errors": [{"status": 404, "url": "http://example.com/start.js", "statusText": "Not Found", "type": "script"}],
        "console_errors": [{"level": "error", "text": "Uncaught ReferenceError: x is not defined", "url": "http://example.com/app.js"}]
    }

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to backend.")
            
            # 1. Send Universal Analyze Request
            await websocket.send(json.dumps(payload))
            print("Sent universal_analyze request.")
            
            # 2. Wait for response
            while True:
                response = await websocket.recv()
                data = json.loads(response)
                print(f"Received: {data.get('type')}")
                
                if data.get("type") == "analysis_result":
                    print("\nSUCCESS: Analysis Result Received!")
                    print("Answer preview:", data.get("answer")[:100])
                    break
                elif data.get("type") == "error":
                    print("\nERROR from Backend:", data.get("message"))
                    break
                    
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Ensure backend is running: uvicorn src.main:app --reload")

if __name__ == "__main__":
    asyncio.run(test_backend())
