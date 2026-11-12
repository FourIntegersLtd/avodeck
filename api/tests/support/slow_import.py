"""A module that takes a while to import, standing in for the pipeline's parsing module (it imports pikepdf, Pillow and
the pipeline package, about 2 s). Used by the sandbox speed tests to show that imports are paid at spawn, not on a call."""

import time

time.sleep(1.5)
LOADED = True
