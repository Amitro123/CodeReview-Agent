# 🛡️ Skill Audit Report: CodeReview-Agent

**Date:** 2026-02-12
**Purpose:** Audit of auto-generated skills to identify hallucinations and missing coverage against `README.md`.

## 📊 Executive Summary
The `project-rules-generator` (Groq provider) created 16 skills.
- **6** are Safe/Accurate.
- **9** are Hallucinated (contain references to non-existent `src/` files).
- **3** Key Technologies are Missing completely.

---

## 🟢 Safe / Accurate Skills
*These skills correctly reflect the high-level architecture described in README.md or are generic enough to be safe.*

| Skill File | Status | Notes |
| :--- | :--- | :--- |
| `perplexity-api.md` | ✅ Safe | Accurate role definition (Backend analysis). |
| `websocket-handler.md` | ✅ Safe | Correct architecture (FastAPI + WebSockets). |
| `fastapi-endpoints.md` | ✅ Safe | Correct context. |
| `openai-api.md` | ✅ Safe | Generic integration context. |
| `systematic-debugging.md` | ✅ Safe | Builtin process. |
| `test-driven-development.md` | ✅ Safe | Builtin process. |
| `code-review.md` | ✅ Safe | Generic (but empty). |

---

## 🔴 Hallucinated Skills (Action Required)
*These skills reference a `src/` directory structure and specific files (e.g., `src/main.py`) that **DO NOT EXIST** in the current project. Using them will confuse the AI.*

| Skill File | Hallucination | Recommendation |
| :--- | :--- | :--- |
| `rate-limiting.md` | Refs `src/main.py`, `fastapi_limiter_keycloak` | **Delete** or Rewrite for actual codebase. |
| `api-client-patterns.md` | Refs `src/api/endpoints/users.py` | **Rewrite** to match actual API structure. |
| `async-patterns.md` | Refs `src/workers/tasks.py` | **Rewrite** with actual async examples. |
| `dependency-injection.md` | Refs `src/api/services/users.py` | **Rewrite** or Delete. |
| `error-handling.md` | Refs `src/api/main.py` | **Rewrite** with actual error handling logic. |
| `middleware-patterns.md` | Refs `src/main.py`, `CORSMiddleware` | **Rewrite** if middleware actually exists. |
| `pydantic-validation.md` | Refs `src/schemas/user.py` | **Rewrite** with actual Pydantic models. |
| `response-parsing.md` | Refs `src/api/utils/response.py` | **Rewrite** or Delete. |
| `retry-error-handling.md` | Refs `aiohttp` (Project might treat this differently) | **Rewrite** with actual HTTP client usage. |

---

## ⚠️ Missing Skills (Gap Analysis)
*Your `README.md` lists these core technologies, but no skills exist for them.*

1.  **Chrome Extensions**
    - Missing patterns for `manifest.json`, `background.js`, `chrome.automation`, and message passing.
2.  **GitPython**
    - Missing patterns for handling git operations, diffs, and repo management.
3.  **MCP (Model Context Protocol)**
    - Missing guidelines for tool integration and server communication.

---

## 💡 Instructions for Claude
**Prompt:**
> "I have audited the generated skills. Please fix the following:
> 1.  **Delete/Rewrite Hallucinations:** The skills listed as 'Hallucinated' reference a `src/` directory that does not exist. Please update them to reflect the *actual* file structure of `CodeReview-Agent` or delete them if they are not relevant.
> 2.  **Fill Gaps:** Create new skills for **Chrome Extensions**, **GitPython**, and **MCP** based on the `README.md` architecture.
> 3.  **Ensure Consistency:** Verify that all examples in the skills actually exist or are clearly marked as 'generic examples' that do not hallucinate project paths."

---

## 📜 Rules & Constitution Audit
*Analysis of `.clinerules/rules.md` and `.clinerules/constitution.md`*

### ✅ `rules.md` (Verdict: Safe)
- **Content:** The High-level "DO/DON'T" rules are **accurate** to the tech stack (FastAPI, Pydantic, GitPython).
- **Structure:** Correctly identifies the project as a `python-cli`.
- **No Hallucinations:** Unlike the skills, the rules file stays high-level and does *not* reference non-existent files like `src/main.py`.
- **Issue:** It *lists* the bad skills in its metadata footer, but the rules themselves are fine.

### ✅ `constitution.md` (Verdict: Safe but Sparse)
- **Content:** minimal high-level principles.
- **Accuracy:** Correctly notes "No test framework detected" (which is true, as there is no standard `tests/` folder with config, though `rules.md` encourages testing).

### 💡 Recommendation for Rules
The rules are in **good shape**. No immediate action is needed for `rules.md` other than regenerating it *after* you fix the skills, so that the metadata footer updates to list the correct skills.

