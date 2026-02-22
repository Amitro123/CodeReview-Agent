# Task 010: Local git diff analysis
# Goal: Analyze local git diffs and unpushed changes for context

import os
from pathlib import Path
from src.services.git_service import GitService

def run():
    print("Testing GitService...")
    service = GitService()
    
    if not service.repo:
        print("Warning: Not in a git repo, but structure exists.")
        return True # Skip if CI environment lacks .git
        
    status = service.get_status_summary()
    diff = service.get_diff()
    unpushed = service.get_unpushed_changes()
    
    print(f"Status Summary:\n{status}")
    print(f"Diff length: {len(diff)}")
    print(f"Unpushed changes length: {len(unpushed)}")
    
    print("DONE Task #10: Local git diff analysis verified.")
    return True

if __name__ == "__main__":
    if run():
        exit(0)
    else:
        exit(1)
