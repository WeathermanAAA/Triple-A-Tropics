# IBTrACS Download Scripts

Use the script that matches your operating system (you get two options with windows, your choice):

- **Windows (PowerShell)** → `fetchIBTrACS.ps1`
- **Windows (CMD)** → `fetchIBTrACS.bat`
- **macOS or Linux** → `fetchIBTrACS.sh`
- **Any OS with Python installed** → `fetchIBTrACS.py`

## How to run

In your terminal, navigate to `~/Triple-A-Tropics/manual-fetch`

### Windows (PowerShell)

    ./fetchIBTrACS.ps1

### Windows (CMD)

    ./fetchIBTrACS.bat

### macOS / Linux

    chmod +x ./fetchIBTrACS.sh
    ./fetchIBTrACS.sh

### Python (works on any OS but needs a python interpreter)

    python fetchIBTrACS.py

## What they do

All four scripts download the same file:

    ibtracs.WP.list.v04r01.csv

Use the script for your OS unless you specifically want the Python version for portability.
