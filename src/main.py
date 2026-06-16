"""
Step Noise Detection — entry point

Run from project root:   python src/main.py
Run from src/:           python main.py
"""
import sys
import os

# When running as a windowed PyInstaller app (console=False), sys.stdout and
# sys.stderr are None.  Some libraries (e.g. numpy.f2py.cfuncs) unconditionally
# write to them during import, causing an AttributeError.  Redirect to devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

# Ensure the src/ directory is on the import path regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from app import StepNoiseApp

if __name__ == '__main__':
    root = tk.Tk()
    app  = StepNoiseApp(root)
    root.mainloop()
