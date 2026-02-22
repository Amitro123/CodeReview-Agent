# Project Roadmap: CodeReview Agent

- [x] Phase 1: Setup and Configuration
- [x] Phase 2: Extension and WebSocket Integration
- [x] Phase 3: Architectural Refactoring (ServiceFactory)
- [x] Phase 4: Local Git & GitHub Actions Integration (chrome.automation)
- [x] Phase 5: MCP & Custom Tools

---

## Phase 1: Setup and Configuration [COMPLETE]

- [x] Task 1: Set up Perplexity API key and GitHub token in settings
- [x] Task 2: Install necessary dependencies and tools
- [x] Task 3: Configuration validation via Pydantic

## Phase 2: Extension & Real-time Integration [COMPLETE]

- [x] Task 1: WebSocket backend endpoint (`/ws/chat`)
- [x] Task 2: Extension UI (Tabbed popup, premium styling)
- [x] Task 3: Secure API key management (chrome.storage.sync)
- [x] Task 4: Real-time message routing and error handling

## Phase 3: Backend Architecture [COMPLETE]

- [x] Task 1: Service Factory implementation
- [x] Task 2: Decoupled AI Client Abstraction (BaseClient)
- [x] Task 3: Dynamic dependency injection of API keys
- [x] Task 4: Automated unit tests for core architecture

## Phase 4: Advanced Automation [COMPLETE]

- [x] Task 1: Use `chrome.automation` API to scrape GitHub Actions logs
- [x] Task 2: Implement local `git diff` analysis via GitPython
- [x] Task 3: Context-aware analysis: PR diffs vs. Action failures

## Phase 5: Customization & MCP [COMPLETE]

- [x] Task 1: Set up MCP servers for customized tool analysis
- [x] Task 2: Integrate GithubAgent and DevLens-AI
- [x] Task 3: Support for customized project rules (rules.md)

## Phase 6: UI Overhaul (Perplexity Style) [COMPLETE]

- [x] Task 1: Implement dark mode glassmorphism theme (`styles.css`).
- [x] Task 2: Refactor popup layout for "Comet" chat-first interface.
- [x] Task 3: Implement sliding settings panel and new interaction logic.
