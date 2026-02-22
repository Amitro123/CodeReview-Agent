import os
import json
import asyncio
from pathlib import Path
from groq import Groq
from src.config import settings

class MultiAgentAnalyzer:
    def __init__(self):
        self.api_key = settings.groq_api_key
        if not self.api_key:
            # Fallback to a placeholder if not set yet, but log warning
            print("WARNING: GROQ_API_KEY not found in environment")
            
        self.client = Groq(api_key=self.api_key) if self.api_key else None
    
    async def _agent(self, model: str, prompt: str):
        if not self.client:
            return "Error: Groq client not initialized. Check GROQ_API_KEY."
            
        print(f"DEBUG: Calling Groq agent with model {model}...")
        # Run in executor because groq-python is synchronous
        loop = asyncio.get_event_loop()
        try:
            chat_completion = await loop.run_in_executor(
                None, 
                lambda: self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                )
            )
            print(f"DEBUG: Groq agent {model} responded.")
            return chat_completion.choices[0].message.content
        except Exception as e:
            print(f"DEBUG: Groq agent {model} failed: {str(e)}")
            return f"Error: {str(e)}"

    async def analyze_ci_failure(self, ci_log: str, repo: str):
        # Define prompts
        fe_prompt = f"Identify Frontend/UI issues in this CI failure log. Detail the issues and suggest code fixes with diffs.\n\nCI Log:\n{ci_log}\n\nRepo: {repo}"
        be_prompt = f"Identify Backend/API/DB issues in this CI failure log. Detail the issues and suggest code fixes with diffs.\n\nCI Log:\n{ci_log}\n\nRepo: {repo}"
        
        # Parallel Execution: Frontend (Fast) + Backend (Fast)
        print("DEBUG: Running Frontend and Backend agents in parallel...", flush=True)
        fe_task = self._agent("llama-3.1-8b-instant", fe_prompt) # Optimized model
        be_task = self._agent("llama-3.1-8b-instant", be_prompt) # Optimized model
        
        fe_analysis, be_analysis = await asyncio.gather(fe_task, be_task)

        # Agent 3: SolutionIntegrator (High Quality)
        integration_prompt = f"Integrate the following Frontend and Backend analysis into a single, unified fix plan. Focus on the root cause and provide a clear PR title and specific file fixes.\n\nFrontend Analysis:\n{fe_analysis}\n\nBackend Analysis:\n{be_analysis}\n\nRepo: {repo}"
        solution_plan = await self._agent("llama-3.3-70b-versatile", integration_prompt)
        
        # Save MDs
        analysis_files = self._save_mds(fe_analysis, be_analysis, solution_plan, repo)
        return {
            "solution": solution_plan,
            "files": analysis_files
        }

    def _save_mds(self, fe, be, solution, repo):
        from src.config import find_repo_root
        root = find_repo_root(repo)
        analysis_dir = root / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        
        fe_path = analysis_dir / "frontend_analysis.md"
        be_path = analysis_dir / "backend_analysis.md"
        sol_path = analysis_dir / "solution_plan.md"
        
        fe_path.write_text(f"# FrontendAgent Findings\n{fe}", encoding="utf-8")
        be_path.write_text(f"# BackendAgent Findings\n{be}", encoding="utf-8")
        sol_path.write_text(f"# Unified Fix Plan\n{solution}", encoding="utf-8")
        
        return {
            "frontend": str(fe_path),
            "backend": str(be_path),
            "solution": str(sol_path)
        }
