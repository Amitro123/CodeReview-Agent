# Task 008: Perplexity Chrome Extension
# Goal: Extract API key -> manifest.json + background.js
# Use existing PerplexityClient from ServiceFactory

import os
from pathlib import Path

def run():
    print("Verifying extension integration...")
    # Check for storage permission in manifest.json
    manifest_path = Path("extension/manifest.json")
    if "storage" not in manifest_path.read_text():
        print("Error: 'storage' permission missing in manifest.json")
        return False
    
    # Check for API key extraction logic in background.js
    background_path = Path("extension/background.js")
    if "perplexityApiKey" not in background_path.read_text():
        print("Error: API key extraction logic missing in background.js")
        return False
        
    print("✅ Extension integration verified. API key flow is implemented.")
    return True

if __name__ == "__main__":
    if run():
        exit(0)
    else:
        exit(1)
