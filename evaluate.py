"""Evaluate the labelled documents against ground truth and print the results table.

    python evaluate.py
    python evaluate.py --score-only          # re-score saved outputs, no LLM calls

Same as: python -m evaluation.run
"""

import sys

from evaluation.run import main

if __name__ == "__main__":
    sys.exit(main())
