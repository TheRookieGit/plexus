import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# The shared code comes from the my-collections submodule, as in server.py.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor'/'my-collections'))
# Never the real shared sign-ins in %LOCALAPPDATA%\CollectionKit; tests that sign in use a folder of their own.
os.environ['COLLECTIONKIT_DIR'] = tempfile.mkdtemp(prefix='collectionkit-')
atexit.register(shutil.rmtree,os.environ['COLLECTIONKIT_DIR'],True)
