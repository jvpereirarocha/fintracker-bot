# fintracker-bot

Bot do Telegram que registra receitas e despesas no [fintracker](../fintracker) a partir de
mensagens em linguagem natural, como:

- "Gastei 20 reais com lanche"
- "Recebi R$ 5.500 de salário do mês"
- "Paguei 150,90 de conta de luz ontem"

## Como funciona

```
Mensagem no Telegram
  → lista as categorias do fintracker (GET /categories/)
  → Claude extrai tipo, valor, descrição, categoria e data (saída estruturada)
  → categoria não reconhecida? o bot mostra botões com as categorias para você escolher
  → POST /transactions/ no fintracker
```

Valores padrão quando a mensagem não diz:

| Campo | Despesa | Receita |
|---|---|---|
| `registrationDate` | hoje (fuso de `TIMEZONE`) | hoje |
| `dueDate` | igual a `registrationDate` | — |
| `status` | `already_paid` | `received` |

O bot usa uma conta fixa do fintracker (`FINTRACKER_USERNAME`/`FINTRACKER_PASSWORD`) e só responde
ao usuário do Telegram configurado em `TELEGRAM_ALLOWED_USER_ID`. O token JWT é renovado
automaticamente quando a API responde 401.

## Rodando localmente

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # preencha os valores
.venv/bin/python -m bot.main
```

## Docker

```bash
docker build -t fintracker-bot .
docker run --env-file .env fintracker-bot
```

## Desenvolvimento

```bash
.venv/bin/pytest
.venv/bin/ruff check . --fix && .venv/bin/ruff format .
.venv/bin/mypy bot
```

## Estrutura

- `bot/main.py`: monta a aplicação do Telegram e injeta os clientes em `bot_data`
- `bot/handlers.py`: handlers de mensagem, escolha de categoria e formatação das respostas
- `bot/parser.py`: extração da transação com Claude (`messages.parse` + Pydantic)
- `bot/fintracker.py`: cliente HTTP da API do fintracker (login, categorias, transações)
- `bot/domain.py`: `TransactionDraft` e formatação de valores/datas no padrão brasileiro
