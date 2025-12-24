import subprocess
import sys
from pathlib import Path


def main():
    """Streamlitアプリを起動するCLIエントリポイント"""
    main_py = Path(__file__).parent / "main.py"
    try:
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(main_py)])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
