"""
Step Noise Detection — entry point

Run from project root:   python src/main.py
Run from src/:           python main.py
"""
import sys
import os

# Ensure the src/ directory is on the import path regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from app import StepNoiseApp

if __name__ == '__main__':
    root = tk.Tk()
    app  = StepNoiseApp(root)
    root.mainloop()
