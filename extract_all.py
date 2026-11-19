"""Write the JSON for every document in data/labelled and data/unlabelled to outputs/.

    python extract_all.py

Same as: python -m backend.app.cli.extract_all
"""

import sys

from backend.app.cli.extract_all import main

if __name__ == "__main__":
    sys.exit(main())
