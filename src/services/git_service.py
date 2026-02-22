import git
from pathlib import Path

class GitService:
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
        try:
            self.repo = git.Repo(repo_path)
        except git.InvalidGitRepositoryError:
            self.repo = None

    def get_diff(self) -> str:
        """Returns the current unstaged diff."""
        if not self.repo:
            return "Not a git repository."
        return self.repo.git.diff()

    def get_unpushed_changes(self) -> str:
        """Returns the diff between the current branch and its upstream."""
        if not self.repo:
            return "Not a git repository."
        try:
            # Get changes between current branch and its tracked upstream
            branch_name = self.repo.active_branch.name
            upstream_name = self.repo.active_branch.tracking_branch().name
            return self.repo.git.diff(f"{upstream_name}..{branch_name}")
        except Exception as e:
            return f"Error retrieving unpushed changes: {str(e)}"

    def get_status_summary(self) -> str:
        """Returns a brief summary of the repo status."""
        if not self.repo:
            return "Not a git repository."
        return self.repo.git.status(short=True)
