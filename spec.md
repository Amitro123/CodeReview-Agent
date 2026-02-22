# CodeReview Agent v1.0 Specification

## Overview
Chrome extension frontend with GitHub automation + FastAPI backend for precise code analysis tailored to your projects.

## Components

### 1. Chrome Extension (Manifest V3)
**Popup**: Chat UI (input, message history, repo selector)
**Background Service Worker**: WebSocket client → `ws://localhost:8000/ws/chat`
**Content Scripts**: 
  - chrome.automation API (mouse control, clicks on GitHub UI)
  - DOM parsing (Actions logs, PR diffs, file contents)
**Storage**: API keys, GitHub tokens, watched repos, chat history

### 2. FastAPI Backend (`localhost:8000`)
Endpoints:
/ws/chat (WebSocket - streaming chat)
/analyze POST {repo, path, diff} → LLM analysis
/github/{repo}/pr/{id} GET Actions logs + status
/local-diff POST directory path → git diff

**Analysis Pipeline**:
1. GitHub API (PRs, Actions logs, file contents)
2. Local git (unpushed changes, status)
3. Perplexity API (`llama-3.1-sonar-huge-128k-online`)
4. MCP client → local tools (pylint, pytest, custom CLI)

### 3. LLM Prompt Templates
Code Review Prompt:
"""
Repo: {repo_name} ({branch})
File: {file_path}
Diff: {diff_content}

Your projects context:

GithubAgent: MCP-based code analysis

DevLens-AI: Video/code architecture analysis

mcp-python-auditor: Python auditing server

Tasks:

Find bugs/security issues/performance problems

Rate 1-10 with explanation

Suggest specific fixes (show diff)

Architecture improvements for scale
"""

## User Flow
Open GitHub PR → Extension popup → "analyze Actions failure"

Extension: chrome.automation → Actions tab → scrape logs

WebSocket → Backend: GitHub API + local git diff

Backend → Perplexity: "analyze this failure in context"

MCP tools → Backend → Streamed response to chat

Response: "BUG: Line 42 null pointer. Fix: [diff]. Score: 7/10"

## Development Milestones
v0.1 (1 day): Extension popup + WS connection + echo chat
v0.2 (2 days): chrome.automation + GitHub navigation + DOM parsing
v0.3 (3 days): FastAPI backend + Perplexity integration + git analysis
v0.4 (2 days): MCP client + local tools integration
v1.0 (3 days): Polish UI, error handling, repo context, voice prep

## Configuration (.env)
PERPLEXITY_API_KEY=pxl_...
GITHUB_TOKEN=ghp_...
WATCHED_REPOS=git@github.com:Amitro123/GithubAgent.git,git@github.com:Amitro123/DevLens-AI.git
MCP_SERVERS=localhost:5000,localhost:5001

## Success Metrics
- 90%+ bug detection accuracy on your repos
- <3s response time for PR analysis  
- Full GitHub Actions automation (logs → root cause → fix)
- Context retention across chat sessions
Ready to run prg plan spec.md from your project-rules-generator! The flow diagram shows the complete architecture visually.

