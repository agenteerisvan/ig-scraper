#!/bin/sh
echo "=== DIAGNOSTICO START ==="
echo "WORKDIR: $(pwd)"
echo ""
echo "--- ROOT LS ---"
ls -la
echo ""
echo "--- APP/ CONTENTS ---"
ls -la app/
echo ""
echo "--- FIRST 5 LINES OF app/web.py ---"
head -5 app/web.py
echo ""
echo "--- IMPORT TEST ---"
python -c "
import sys, traceback
sys.path.insert(0, '.')
try:
    import app.web
    print('SUCCESS: app.web imported OK')
except Exception:
    traceback.print_exc()
"
echo ""
echo "--- INSTALLED PKGS (key ones) ---"
pip list 2>/dev/null | grep -iE "fastapi|uvicorn|instagrapi|dotenv|httpx"
echo ""
echo "=== DIAGNOSTICO END ==="
echo ""
exec uvicorn app.web:app --host 0.0.0.0 --port 8000
