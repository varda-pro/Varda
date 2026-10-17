"""``python -m varda.crism``: the CRISM command line."""

import sys

from varda.crism.cli import crismMain

sys.exit(crismMain(sys.argv[1:]))
