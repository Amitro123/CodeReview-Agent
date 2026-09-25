# 🚀 How to Run CodeReview-Agent Independently

Follow these steps to fully utilize the **Multi-Agent CI Analyzer** and **Universal Browser Agent**.

## 1. Start the Backend (The "Brain")
1.  Open a terminal in the folder: `C:\Users\USER\.gemini\antigravity\scratch\CodeReview-Agent`.
2.  Run the following command:
    ```bash
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    ```
    ```
    > [!NOTE]
    > You should see `Application startup complete.` and your first `ping` messages once the extension connects.

## 2. Load/Update the Extension (The "Eyes")
Screenshots are captured natively by the extension itself (`chrome.tabs.captureVisibleTab`) -
no separate screenshot service is needed. This captures exactly what you see in your
logged-in tab, and the image is sent straight to the configured vision model for analysis.
If you have made code changes or I have just fixed a bug in the extension:
1.  Open Chrome and navigate to `chrome://extensions`.
2.  Enable **Developer Mode** (top right toggle).
3.  Find **CodeReview Agent**.
4.  Click the **Reload Icon** (circular arrow) to ensure the latest fixes are active.

---

## 3. Connect & Analyze
1.  **Open the Popup**: Click the CodeReview Agent icon in your browser toolbar.
2.  **Settings Tab**:
    - Ensure **Backend URL** is `ws://localhost:8000`.
    - The agents use the LLM key from the backend's `.env` (`OPENROUTER_API_KEY` by default), not this panel. The **Perplexity API Key** field is optional and only used by the older URL/log analysis actions.
    - Click **Save Settings**. (This establishes the WebSocket connection).
3.  **Main Tab**:
    - **Ask about any page**: type what's wrong in the chat box (e.g. *"the orders list shows an error"*) and press Enter.
      The panel first shows where the problem was routed (e.g. *Routed to: Backend (Backend 92%, Jev)*), then the fix plan.
      If the router isn't sure, it asks you to pick; under each result, **Wrong area? Re-run as:** runs another agent.
    - **Failed CI run**: open the run on Azure DevOps (`.../_build/results?buildId=...`) or GitHub Actions and click
      **CI Logs** (or just ask). The failing steps and their logs are read from the CI system's API.

---

## 🛠 Troubleshooting
- **Nothing Happening?**: Reload the extension in `chrome://extensions` and click **Save Settings** again.
- **Port 8000 Error?**: If the terminal says "Address already in use", run `taskkill /F /IM python.exe` in PowerShell to clear old instances.
- **Model Errors?**: Ensure your `.env` has a valid `OPENROUTER_API_KEY` (or the key for your `LLM_PROVIDER`), and that `VISION_MODEL` accepts images and `CODE_MODEL` supports tool calling.

---
*Built for production stability by Antigravity.*
