"""
Sims 4 DLC Archive & Install Manager (White Label Edition).
Main entry point.
"""
import sys
import os

# Stage 1: Runtime environment and foundation checks
# Mandated by PROJECT_SPEC.md section 3: "Базовый интерпретатор: Python 3.12 x64"
MIN_PYTHON_VERSION = (3, 12)

def main():
    if sys.version_info < MIN_PYTHON_VERSION:
        print(
            f"Error: Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ x64 is required. "
            f"Current version is {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.",
            file=sys.stderr
        )
        sys.exit(1)

    if sys.maxsize <= 2**32:
        print("Error: 64-bit operating system and Python x64 runtime are required.", file=sys.stderr)
        sys.exit(1)

    print("Sims 4 DLC Archive & Install Manager (Stage 1 Core Foundation)")

if __name__ == "__main__":
    main()
