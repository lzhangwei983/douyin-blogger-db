"""All tests isolate app.py module-import database initialization."""
import os
import shutil
import tempfile
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
for directory in (ROOT/"core",ROOT/"work"):
    if str(directory) not in os.sys.path:
        os.sys.path.insert(0,str(directory))
TEST_DATA_HOME=Path(tempfile.gettempdir())/("douyin-public-tests-"+uuid.uuid4().hex)
TEST_DATA_HOME.mkdir(parents=True,exist_ok=False)
os.environ["DYDB_HOME"]=str(TEST_DATA_HOME)


def pytest_sessionfinish(session,exitstatus):
    data_home=TEST_DATA_HOME.resolve()
    temp_root=Path(tempfile.gettempdir()).resolve()
    if data_home.parent==temp_root and data_home.name.startswith("douyin-public-tests-"):
        shutil.rmtree(data_home,ignore_errors=True)
