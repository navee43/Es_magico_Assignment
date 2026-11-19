"""Extract one invoice and print the JSON to stdout.

    python extract.py invoice.pdf > out.json
    python extract.py scan.jpg --full        # also print review reasons, tokens and cost

Same as: python -m backend.app.cli.extract
"""

import sys

from backend.app.cli.extract import main

if __name__ == "__main__":
    sys.exit(main())
