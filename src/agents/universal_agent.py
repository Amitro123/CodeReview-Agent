import os
import asyncio
from typing import Dict, Any
from groq import Groq
from src.config import settings
import json

class UniversalAgent:
    def __init__(self):
        self.api_key = settings.groq_api_key
        self.client = Groq(api_key=self.api_key) if self.api_key else None

    async def _agent(self, model: str, prompt: str, image_data_url: str = None):
        if not self.client:
            print("DEBUG: UniversalAgent - Groq client not initialized.", flush=True)
            return "Error: Groq client not initialized."

        if image_data_url:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        else:
            content = prompt

        print(f"DEBUG: UniversalAgent - Calling {model}...", flush=True)
        loop = asyncio.get_event_loop()
        try:
            completion = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    messages=[{"role": "user", "content": content}],
                    model=model,
                )
            )
            print(f"DEBUG: UniversalAgent - {model} responded.", flush=True)
            return completion.choices[0].message.content
        except Exception as e:
            print(f"DEBUG: UniversalAgent - {model} failed: {str(e)}", flush=True)
            return f"Error: {str(e)}"

    async def visual_agent(self, screenshot: str, query: str, dom: Dict[str, Any], network_errors: list = [], console_errors: list = []):
        # `screenshot` is captured by the extension itself via chrome.tabs.captureVisibleTab,
        # i.e. exactly what the user's logged-in tab is showing right now.
        prompt = f"""
        User Query: "{query}"
        DOM Context: {json.dumps(dom, indent=2)}
        Network Errors (DevTools): {json.dumps(network_errors, indent=2)}
        Console Errors (DevTools): {json.dumps(console_errors, indent=2)}

        Task:
        1. FIRST: Analyze the User Query to determine intent.
           - If query mentions "error", "broken", "connection", "fail", "data", "loading": HIGH PRIORITY on Network/Console errors.
           - If query mentions "style", "color", "move", "text", "UI": LOW PRIORITY on Network/Console errors (unless they block the UI).
        2. Analyze the attached screenshot together with the DOM state, based on that priority.
        3. IGNORE standard background noise (analytics, tracking) unless it's the specific root cause.
        4. ANSWER the User Query directly.
        """
        model = settings.groq_vision_model if screenshot else "llama-3.3-70b-versatile"
        return await self._agent(model, prompt, image_data_url=screenshot)

    async def code_agent(self, repo: str, ui_analysis: str, selected_element: Dict[str, Any]):
        prompt = f"""
        Repo: {repo}
        UI Analysis: {ui_analysis}
        Target Element: {selected_element}
        
        Task:
        1. Map the UI analysis to specific source files in the repository.
        2. Identify the React components, CSS classes, or Backend props involved.
        3. Propose code-level changes.
        """
        return await self._agent("llama-3.1-8b-instant", prompt)

    async def integrator(self, ui_analysis: str, code_analysis: str):
        prompt = f"""
        Frontend/UI Analysis: {ui_analysis}
        Backend/Code Analysis: {code_analysis}
        
        Task:
        1. Integrate these into a single, unified "v1.1 Universal Fix Plan".
        2. CONTEXT AWARENESS:
           - If the UI Analysis identified critical Network/Console errors RELEVANT to the user's request -> Include them as Root Cause.
           - If the errors were deemed "background noise" -> Filter them out completely.
        3. Provide a clear 'Root Cause' and 'Fix Checklist'.
        4. Output exactly what an IDE agent needs to do.
        """
        return await self._agent("llama-3.3-70b-versatile", prompt)

    def save_universal_mds(self, fix_plan: str, query: str, repo: str, network_errors: list = [], console_errors: list = [], screenshot: str = None):
        from src.config import find_repo_root
        from datetime import datetime
        
        root = find_repo_root(repo)
        analysis_dir = root / "analysis" / "universal"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        safe_query = "".join([c if c.isalnum() else "-" for c in query[:20]])
        
        fix_path = analysis_dir / f"universal-{timestamp}-{safe_query}-fix.md"
        
        # Format the content
        md_content = f"# Universal Fix Plan\n\nQuery: {query}\n\n"
        
        if screenshot:
            md_content += f"## Screenshot\n![Screenshot]({screenshot})\n\n"
            
        if network_errors:
            md_content += f"## DevTools Network Errors\n"
            for err in network_errors:
                md_content += f"- **{err.get('status')}** {err.get('url')} ({err.get('statusText')})\n"
            md_content += "\n"
            
        if console_errors:
            md_content += f"## DevTools Console Errors\n"
            for err in console_errors:
                md_content += f"- [{err.get('level')}] {err.get('text')} ({err.get('url')})\n"
            md_content += "\n"
        
        md_content += f"{fix_plan}\n"
        fix_path.write_text(md_content, encoding="utf-8")
        
        return str(fix_path)
