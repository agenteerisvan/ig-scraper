"""Extração de telefones e e-mails de texto (bio)."""
import re

# Padrões de telefone BR:
#   (11) 99999-9999  |  11 99999-9999  |  +55 11 99999-9999
#   31 3333-4444     |  0800 123 4567  |  11999999999
BR_PHONE_PATTERN = re.compile(
    r'(?:\+?55[\s.-]?)?'              # DDI opcional
    r'(?:0800[\s.-]?)?'               # 0800 opcional
    r'\(?\b(\d{2})\)?'             # DDD ou prefixo 0800
    r'[\s.-]?'                         # separador
    r'(\d{4,5})'                       # sequência média
    r'[\s.-]?'                         # separador
    r'(\d{4})\b'                      # final
)

BR_PHONE_0800 = re.compile(
    r'(?:0800[\s.-]?)(\d{3,4})[\s.-]?(\d{3,4})\b'
)

EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)


def extract_phone(text: str) -> str | None:
    """Extrai o primeiro telefone BR válido encontrado no texto."""
    if not text:
        return None

    # Tenta padrão 0800 primeiro
    m0800 = BR_PHONE_0800.search(text)
    if m0800:
        return f"0800 {m0800.group(1)}-{m0800.group(2)}"

    # Tenta celular/fixo
    match = BR_PHONE_PATTERN.search(text)
    if match:
        ddd, mid, last = match.group(1), match.group(2), match.group(3)
        # Validação básica: DDD não pode ser 00 e mid não pode começar com 0
        if ddd in ("00", "0") or mid.startswith("0"):
            return None
        if len(mid) == 5 and not mid.startswith("9"):
            # Provavelmente falso positivo (ex: data, CEP)
            return None
        return f"({ddd}) {mid}-{last}"

    return None


def extract_email(text: str) -> str | None:
    """Extrai o primeiro e-mail válido encontrado no texto."""
    if not text:
        return None
    m = EMAIL_PATTERN.search(text)
    if m:
        return m.group(0).lower()
    return None


# --- Tests rápidos ---
if __name__ == "__main__":
    print("=== TELEFONE ===")
    tests_phone = [
        ("Contato: (11) 99999-8888", "(11) 99999-8888"),
        ("WhatsApp 11 98888-7777", "(11) 98888-7777"),
        ("+55 21 97777-6666", "(21) 97777-6666"),
        ("Me chama: 31 3222-1111", "(31) 3222-1111"),
        ("0800 123 4567", "0800 123-4567"),
        ("Nada aqui", None),
        ("Nasci em 1990", None),
    ]
    for text, expected in tests_phone:
        result = extract_phone(text)
        status = "OK" if result == expected else "FALHOU"
        print(f"  {status} {text!r:40s} -> {result!r}")

    print("\n=== EMAIL ===")
    tests_email = [
        ("Contact helder@gmail.com for info", "helder@gmail.com"),
        ("Email: joao.silva@exemplo.com.br", "joao.silva@exemplo.com.br"),
        ("Sem nada", None),
        ("", None),
    ]
    for text, expected in tests_email:
        result = extract_email(text)
        status = "OK" if result == expected else "FALHOU"
        print(f"  {status} {text!r:45s} -> {result!r}")
