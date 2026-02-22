# AMIT CODING PREFERENCES v1.1.0
## Session History
- 2026-02-12: Session initialized.
- 2026-02-15: Stabilization of Groq Multi-Agent and Browser Extension.

## Preferences
### Learned
- ❌ **Rejected**: Using decommissioned Groq models like `llama3-groq-70b-8192`.
- ✅ **Approved**: Using `llama-3.3-70b-versatile` (Large) and `llama-3.1-8b-instant` (Small) for Multi-Agent pipelines.
- ❌ **Rejected**: Non-returning promises in extension `background.js` (causes "silent" message drops).
- ✅ **Approved**: Always return `connectingPromise` in `connect()` to handle race conditions.
- 🛡️ **Production Rule**: Use `flush=True` in Python `print` statements within WebSocket loops for real-time log visibility.
- 🛡️ **Production Rule**: Kill all uvicorn processes on port 8000 before a clean restart to avoid "Ghost Connections".

### Rejected
- Emojis in terminal logs (causes `UnicodeEncodeError` on some Windows terminals).

### Approved
- 3-Agent Orchestration (Visual -> Code -> Integrator) for complex CI failure analysis.
