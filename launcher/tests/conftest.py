import sys
from pathlib import Path

# The launcher imports the FastAPI app package (``app.*``) which lives in
# backend/. PyInstaller gets this via --paths backend; tests get it here.
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
