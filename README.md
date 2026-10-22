\
# Instagram Follower Scraper

Extrai **nome** e **telefone** dos seguidores de qualquer conta pública do Instagram.

Só digita o `@username` e ele faz o resto.

## Como funciona

1. Faz login no Instagram com uma conta dedicada (headless browser)
2. Abre o perfil do influencer e coleta a lista de seguidores
3. Visita cada perfil individual e extrai o texto da bio
4. Aplica regex para encontrar telefones brasileiros na bio
5. Gera um CSV com os resultados

## Requisitos

- Docker + Docker Compose (recomendado)
- Uma conta Instagram pra servir de scraper (pode ser uma conta nova)

## Setup rápido (Docker)

```bash
# 1. Clone / baixe o projeto
cd instagram-follower-scraper

# 2. Configure as credenciais
cp .env.example .env
nano .env   # coloque seu IG_USERNAME e IG_PASSWORD

# 3. Rode!
docker compose up
```

Pronto. Os CSVs aparecem em `./output/`.

## Uso local (sem Docker)

```bash
# 1. Cria venv
python -m venv .venv
source .venv/bin/activate

# 2. Instala deps
pip install -r requirements.txt
playwright install chromium

# 3. Configura
cp .env.example .env
nano .env

# 4. Rode
python -m app.main @heelder_
```

## Opções de CLI

| Flag | Descrição | Default |
|------|-----------|---------|
| `--max N` | Processa apenas os primeiros N seguidores | todos |
| `--delay S` | Pausa entre perfis em segundos | 3 |
| `--workers N` | Browsers simultâneos | 2 |

Exemplos:

```bash
# Só primeiros 500 seguidores, pausa de 2s
python -m app.main @heelder_ --max 500 --delay 2

# 4 workers (mais rápido mas mais risco de rate limit)
python -m app.main @heelder_ --workers 4
```

## Estrutura de saída

Após executar, a pasta `./output/` contém:

| Arquivo | Conteúdo |
|---------|----------|
| `{username}_followers.txt` | Lista bruta de usernames coletados |
| `resultado_{username}.csv` | CSV completo: nome, username, telefone, privado, bio |
| `resultado_{username}_com_telefone.csv` | CSV filtrado: só quem tem telefone |

### Formato do CSV

```csv
name,username,phone,private,bio_snippet
Maria Silva,maria_silva,"(11) 99999-8888",false,"Consultora financeira 📊 | Contato: (11)...
João Santos,joao_santos,,false,"Fotógrafo | São Paulo
Ana Costa,,true,
```

## Reset de sessão

Se a sessão do Instagram expirar (após dias sem uso):

```bash
rm data/ig_cookies.json
docker compose up   # vai fazer login de novo
```

## Estrutura do projeto

```
instagram-follower-scraper/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .env              ← suas credenciais (não versionar)
├── app/
│   ├── __init__.py
│   ├── main.py       ← CLI / entry point
│   ├── scraper.py    ← lógica core (login, lista, perfil)
│   ├── phone.py      ← regex de telefones BR
│   └── config.py     ← variáveis de ambiente
├── output/           ← CSVs gerados
└── data/             ← cookies salvos
```

## Notas operacionais

- **Perfis privados**: se um seguidor tem conta privada, você captura o username mas não a bio (e portanto não o telefone). O CSV marca `private=true`.
- **Rate limit**: o Instagram pode limitar se você acessar muito rápido. Se aparecer "Check your Instagram", aumente `--delay` pra 5-8s.
- **Conta do scraper**: use uma conta antiga (mais de 2 semanas de vida) se possível. Contas muito novas têm mais chance de ser questionadas.
- **Volume**: para 50K+ seguidores, considere rodar em lotes (`--max 10000`) e concatenar depois.
