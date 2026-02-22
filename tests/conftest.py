import os
import pytest

# Set required environment variables for tests
os.environ["PERPLEXITY_API_KEY"] = "dummy-key-for-testing"
os.environ["GITHUB_TOKEN"] = "dummy-github-token"
