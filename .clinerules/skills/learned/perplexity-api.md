# Perplexity Api

**Project:** codereview-agent

## Purpose

How codereview-agent uses perplexity: Backend analyzes with Perplexity: "Line 42: null pointer, fix with X"

## Auto-Trigger

- Working with perplexity integration code
- Editing files that import or configure perplexity

## Guidelines

- Backend analysis: Perplexity API + git diff + MCP for local tools
- Customized for your projects: GithubAgent, DevLens-AI, MCP servers
- Add Perplexity API key + GitHub token in settings
- Backend analyzes with Perplexity: "Line 42: null pointer, fix with X"
- Backend: FastAPI + WebSocket + Perplexity API (sonar-huge model)
- Tools: GitHub API, gitpython, MCP protocol for local agents
- Handle perplexity errors with proper retries and fallbacks
- Add tests for perplexity integration code

## Project Context (from README)

> - Backend analysis: Perplexity API + git diff + MCP for local tools
> - Customized for your projects: GithubAgent, DevLens-AI, MCP servers
> Add Perplexity API key + GitHub token in settings
> **Extension** → **WebSocket** → **FastAPI Backend** → **Perplexity API + GitHub API + MCP Tools** → **Streaming Response**
> → Backend analyzes with Perplexity: "Line 42: null pointer, fix with X"
> - **Backend**: FastAPI + WebSocket + Perplexity API (sonar-huge model)
> - **Tools**: GitHub API, gitpython, MCP protocol for local agents
