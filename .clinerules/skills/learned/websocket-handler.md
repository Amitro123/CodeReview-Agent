# Websocket Handler

**Project:** codereview-agent

## Purpose

How codereview-agent uses websocket: Backend: FastAPI + WebSocket + Perplexity API (sonar-huge model)

## Auto-Trigger

- Working with websocket integration code
- Editing files that import or configure websocket

## Guidelines

- Real-time streaming responses via WebSocket
- Backend: FastAPI + WebSocket + Perplexity API (sonar-huge model)
- Tools: GitHub API, gitpython, MCP protocol for local agents
- Handle websocket errors with proper retries and fallbacks
- Add tests for websocket integration code

## Project Context (from README)

> - Real-time streaming responses via WebSocket
> pip install fastapi uvicorn openai gitpython websockets
> uvicorn main:app --host 0.0.0.0 --port 8000 --reload
> **Extension** → **WebSocket** → **FastAPI Backend** → **Perplexity API + GitHub API + MCP Tools** → **Streaming Response**
> - **Backend**: FastAPI + WebSocket + Perplexity API (sonar-huge model)
> - **Tools**: GitHub API, gitpython, MCP protocol for local agents
