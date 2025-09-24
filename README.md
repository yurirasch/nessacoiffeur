# Nessa Coiffeur – Agenda (PT-BR)

App em Streamlit conectado ao Google Planilhas para agendamento do salão.

## Recursos
- Perfis: Admin, Funcionário, Cliente
- Bloqueios de agenda e override de horário
- Duração padrão 60 min (cliente) e custom para equipe
- Preços/promos apenas visíveis para equipe/admin
- Robô (Apps Script) para registrar agendamentos digitados nas abas mensais

## Rodar local
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Segredos (NÃO COMMITAR)
Crie `.streamlit/secrets.toml` com:
```toml
sheet_id = "ID_DA_SUA_PLANILHA"

[gcp_service_account]
type = "service_account"
project_id = ""
private_key_id = ""
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = ""
client_id = ""
token_uri = "https://oauth2.googleapis.com/token"
```
Depois, compartilhe a planilha com o e-mail do `client_email` (permissão de Editor).

## Estrutura da planilha
Abas obrigatórias: `CLIENTES`, `SERVICOS`, `FUNCIONARIOS`, `DB_AGENDAMENTOS`, `BLOQUEIOS` e suas abas mensais (ex.: `outubro 2025`).

Modelos CSV em `planilha_modelo/`.

## Apps Script
O arquivo `apps_script/code.gs` contém o script `onEdit` que registra em `DB_AGENDAMENTOS` e atualiza `CLIENTES`. Cole em Extensões → Apps Script na planilha.
