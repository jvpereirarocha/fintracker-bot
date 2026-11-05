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

## Comandos

- `/pendentes`: lista as despesas com status `not_paid` ou `paying`, ordenadas por vencimento
  (marcando as vencidas), com o total e um botão por despesa para marcá-la como paga. Antes de
  atualizar, o bot relê a transação no fintracker, para não sobrescrever alterações feitas em
  outro lugar.
- `/ultimas`: as 10 últimas transações cadastradas, pelo bot ou pela interface web, da mais nova
  para a mais antiga, com valor, data de registro, categoria e status. A ordem de cadastro vem do
  `orderBy=id` do `GET /transactions/`, que exige uma versão do fintracker que tenha esse parâmetro.
- `/help`: exemplos de uso.

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

## Kubernetes (homelab)

Os manifests em `k8s/` rodam o bot no namespace `fintracker-dev`, ao lado da API, que é acessada
pelo Service `fintracker-svc`. O bot usa uma réplica só, com estratégia `Recreate`: o Telegram aceita
um único consumidor de *polling* por token, e as escolhas de categoria pendentes ficam em memória.

```bash
# 1. Publicar a imagem
docker build -t jvrochadev/fintracker-bot:latest .
docker push jvrochadev/fintracker-bot:latest

# 2. Segredos (k8s/bot-secrets.yaml é ignorado pelo git)
cp k8s/bot-secrets.example.yaml k8s/bot-secrets.yaml   # preencha os valores

# 3. Aplicar
kubectl apply -f k8s/bot-configmap.yaml -f k8s/bot-secrets.yaml -f k8s/bot-deployment.yaml
kubectl -n fintracker-dev logs -f deploy/fintracker-bot-deployment
```

Para atualizar depois de publicar uma imagem nova:
`kubectl -n fintracker-dev rollout restart deploy/fintracker-bot-deployment`.

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
