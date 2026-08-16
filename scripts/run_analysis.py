#!/usr/bin/env python3

"""
Simple script to analyze the Harry Potter tuber script and generate insights for virtual tuber performance.
"""

import subprocess
import sys
import os

def run_analysis():
    """Run the character analysis script."""

    try:
        # Run the analysis script
        result = subprocess.run([
            sys.executable,
            '/home/secus/codeProjects/virtualTubers/scripts/harry_potter_character_analyzer.py'
        ], capture_output=True, text=True, cwd='/home/secus/codeProjects/virtualTubers')

        if result.returncode == 0:
            print("Analysis completed successfully!")
            print("Output:")
            print(result.stdout)
            if result.stderr:
                print("Errors:")
                print(result.stderr)
        else:
            print("Analysis failed!")
            print("Error output:")
            print(result.stderr)

    except Exception as e:
        print(f"Error running analysis: {e}")

if __name__ == "__main__":
    run_analysis()