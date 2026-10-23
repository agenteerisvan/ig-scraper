#!/bin/bash
set -e

echo "=== IG Follower Scraper - Start ==="
echo "Python: $(python3 --version)"

# Imprime versoes principais
pip show instagrapi fastapi uvicorn 2>/dev/null | grep -E "^(Name|Version)" || true

# Se instagrapi < 3.2, faz upgrade
INSTAVER=$(python3 -c "import instagrapi; print(instagrapi.__version__)" 2>/dev/null || echo "0.0.0")
echo "instagrapi versao: $INSTAVER"
if [ "$INSTAVER" = "0.0.0" ] || python3 -c "
import re, sys
v = '$INSTAVER'.split('.')
parts = [int(x) for x in re.findall(r'\d+', v)[:2]] + [0]
sys.exit(0 if tuple(parts) >= (3,2) else 1)
" 2>/dev/null; then
    if python3 -c "
import re, sys
v = '$INSTAVER'.split('.')
parts = [int(x) for x in re.findall(r'\d+', v)[:2]] + [0]
sys.exit(0 if tuple(parts) < (3,2) else 1)
" 2>/dev/null; then
        echo "Fazendo upgrade do instagrapi..."
        pip install --upgrade --no-cache-dir instagrapi
        echo "Novo instagrapi: $(python3 -c 'import instagrapi; print(instagrapi.__version__)')"
    fi
fi

cd /app
echo "=== Iniciando servidor ==="
exec uvicorn app.web:app --host 0.0.0.0 --port 8000
