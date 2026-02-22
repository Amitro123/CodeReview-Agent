# Fastapi Endpoints

**Project:** codereview-agent

## Purpose

How codereview-agent uses fastapi: AI-powered code analysis, bug detection, and improvement agent – Chrome extension + FastAPI backend.

## Auto-Trigger

- Working with fastapi integration code
- Editing files that import or configure fastapi

## Guidelines

- AI-powered code analysis, bug detection, and improvement agent – Chrome extension + FastAPI backend.
- Backend: FastAPI + WebSocket + Perplexity API (sonar-huge model)
- Tools: GitHub API, gitpython, MCP protocol for local agents
- Handle fastapi errors with proper retries and fallbacks
- Add tests for fastapi integration code

## Project Context (from README)

> AI-powered code analysis, bug detection, and improvement agent – Chrome extension + FastAPI backend.
> pip install fastapi uvicorn openai gitpython websockets
> uvicorn main:app --host 0.0.0.0 --port 8000 --reload
> **Extension** → **WebSocket** → **FastAPI Backend** → **Perplexity API + GitHub API + MCP Tools** → **Streaming Response**
> - **Backend**: FastAPI + WebSocket + Perplexity API (sonar-huge model)
> - **Tools**: GitHub API, gitpython, MCP protocol for local agents
