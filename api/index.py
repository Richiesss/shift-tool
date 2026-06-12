import os
import sys

# Add project root directory to the python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from web_app import create_app

app = create_app()
