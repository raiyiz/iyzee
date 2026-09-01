import sys
from pathlib import Path

# The project uses a src layout while the instrument modules currently use
# direct local imports. Keep tests runnable from a checkout without hardware.
src_dir = Path(__file__).parents[1] / "src" / "iyzee"
sys.path.insert(0, str(src_dir))
