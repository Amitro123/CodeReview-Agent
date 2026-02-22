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

## 2. Start the Screenshot Service (The "Lens")
1.  Open a **second** terminal.
2.  Install dependencies (only once):
    ```bash
    npm install
    ```
3.  Start the service:
    ```bash
    node screenshot-server.js
    ```
    > [!IMPORTANT]
    > This service must be running for high-quality screenshots and visual analysis.

---

## 3. Load/Update the Extension (The "Eyes")
If you have made code changes or I have just fixed a bug in the extension:
1.  Open Chrome and navigate to `chrome://extensions`.
2.  Enable **Developer Mode** (top right toggle).
3.  Find **CodeReview Agent**.
4.  Click the **Reload Icon** (circular arrow) to ensure the latest fixes are active.

---

## 4. Connect & Analyze
1.  **Open the Popup**: Click the CodeReview Agent icon in your browser toolbar.
2.  **Settings Tab**:
    - Ensure **Backend URL** is `ws://localhost:8000`.
    - Paste your **Perplexity API Key**.
    - Click **Save Settings**. (This establishes the WebSocket connection).
3.  **Main Tab**:
    - **Multi-Agent CI**: On a GitHub Actions page, click **Analyze CI Failure**. Look at your terminal to see the Groq agents wake up!
    - **Universal Chat**: Type any query in the bottom chat box (e.g., *"How does this button work?"*) and press Enter.

---

## 🛠 Troubleshooting
- **Nothing Happening?**: Reload the extension in `chrome://extensions` and click **Save Settings** again.
- **Port 8000 Error?**: If the terminal says "Address already in use", run `taskkill /F /IM python.exe` in PowerShell to clear old instances.
- **Model Errors?**: Ensure your `.env` has a valid `GROQ_API_KEY`.

---
*Built for production stability by Antigravity.*
