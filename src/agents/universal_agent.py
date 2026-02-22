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

    async def _agent(self, model: str, prompt: str):
        if not self.client:
            print("DEBUG: UniversalAgent - Groq client not initialized.", flush=True)
            return "Error: Groq client not initialized."
        
        print(f"DEBUG: UniversalAgent - Calling {model}...", flush=True)
        loop = asyncio.get_event_loop()
        try:
            completion = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                )
            )
            print(f"DEBUG: UniversalAgent - {model} responded.", flush=True)
            return completion.choices[0].message.content
        except Exception as e:
            print(f"DEBUG: UniversalAgent - {model} failed: {str(e)}", flush=True)
            return f"Error: {str(e)}"

    async def take_puppeteer_screenshot(self, url: str) -> str:
        import aiohttp
        print(f"DEBUG: Requesting Puppeteer screenshot for {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post('http://localhost:3001/screenshot', json={'url': url}, timeout=45) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('screenshot')
                    else:
                        print(f"Puppeteer error: {await resp.text()}")
        except Exception as e:
            print(f"Puppeteer screenshot failed: {e}")
        return None

    async def visual_agent(self, screenshot: str, query: str, dom: Dict[str, Any], network_errors: list = [], console_errors: list = []):
        # Try to get a better screenshot from Puppeteer if URL is available
        url = dom.get("url")
        if url:
            pup_screenshot = await self.take_puppeteer_screenshot(url)
            if pup_screenshot:
                screenshot = pup_screenshot

        prompt = f"""
        User Query: "{query}"
        DOM Context: {json.dumps(dom, indent=2)}
        Network Errors (DevTools): {json.dumps(network_errors, indent=2)}
        Console Errors (DevTools): {json.dumps(console_errors, indent=2)}
        Screenshot: [Base64 provided in context]
        
        Task: 
        1. FIRST: Analyze the User Query to determine intent.
           - If query mentions "error", "broken", "connection", "fail", "data", "loading": HIGH PRIORITY on Network/Console errors.
           - If query mentions "style", "color", "move", "text", "UI": LOW PRIORITY on Network/Console errors (unless they block        Task: 
        1. FIRST: Analyze the User Query to determine intent.
           - If query mentions "error", "broken", "connection", "fail", "data", "loading": HIGH PRIORITY on Network/Console errors.
           - If query mentions "style", "color", "move", "text", "UI": LOW PRIORITY on Network/Console errors (unless they block the UI).
        2. Analyze the Visual/DOM state based on that priority.
        3. IGNORE standard background noise (analytics, tracking) unless it's the specific root cause.
        4. ANSWER the User Query directly.
        """
        # Note: In a real vision scenario, we'd send the image to a vision model.
        # For this MVP, we analyze the DOM + HTML metadata provided.
        return await self._agent("llama-3.3-70b-versatile", prompt)

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
