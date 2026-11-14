# Vercel entrypoint: every request (vercel.json routes /(.*) here) goes to the same handler the laptop server uses.
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from primesight.server import H  # noqa: E402


class handler(H):  # Vercel looks for a top-level class named handler in this file (an import alone isn't found)
    pass
