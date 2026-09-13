@echo off
setlocal
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if "%DATABASE_URL%"=="" set DATABASE_URL=postgresql+psycopg://black_rhino:CHANGE_THIS_PASSWORD@localhost:5432/black_rhino
if "%APP_SECRET%"=="" set APP_SECRET=CHANGE_THIS_TO_A_LONG_RANDOM_SECRET
echo.
echo Black Rhino POS requires PostgreSQL.
echo Edit this file or set DATABASE_URL and APP_SECRET before production use.
echo.
python scripts\seed.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
