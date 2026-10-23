#!/bin/sh
echo "=== DIAGNOSTICO ==="
echo "WORKDIR: $(pwd)"
echo ""
echo "--- PROBUSCANDO web.py NO CONTAINER ---"
find / -name "web.py" 2>/dev/null
echo ""
echo "--- PROBUSCANDO __init__.py ---"
find / -path "*/app/__init__.py" 2>/dev/null
echo ""
echo "--- LS RAIZ DO WORKDIR ---"
ls -la
echo ""
echo "--- LS ./app/ ---"
ls -la ./app/ 2>/dev/null || echo "PASTA app/ NAO EXISTE em $(pwd)"
echo ""
echo "--- TENTATIVA DE IMPORT COM PATH EXPLICITO ---"
python -c "
import sys
sys.path.insert(0, '$(pwd)')
print('Python path:', sys.path[:3])
try:
    import app
    print('app package encontrado em:', app.__file__)
except Exception as e:
    print('Falha importar app:', e)
try:
    import app.web
    print('app.web OK')
except Exception as e:
    print('Falha importar app.web:', e)
"
echo ""
echo "=== FIM ==="
echo ""
exec uvicorn app.web:app --host 0.0.0.0 --port 8000
