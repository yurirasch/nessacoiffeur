# app.py — Nessa Coiffeur (PT-BR) — Google Sheets + Streamlit
import os
import hmac
import hashlib
import streamlit as st
import pandas as pd
import datetime as dt
from dateutil import tz
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Nessa Coiffeur - Agenda", layout="wide")
st.set_option("client.showErrorDetails", True)

# ========= Conexão Google Sheets =========
def gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        st.secrets["gcp_service_account"], scope
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=30)
def open_sheet():
    cli = gs_client()
    return cli.open_by_key(st.secrets["sheet_id"])

def read_df(sh, tab):
    ws = sh.worksheet(tab)
    df = pd.DataFrame(ws.get_all_records())
    return df, ws

def append_row(ws, d: dict):
    headers = ws.row_values(1)
    row = [d.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")

def get_header_map(ws):
    headers = ws.row_values(1)
    return {h: i + 1 for i, h in enumerate(headers)}

def update_employee_password(ws, username: str, new_hash: str, must_change=False):
    cols = get_header_map(ws)
    user_col = cols.get("username")
    pw_col = cols.get("password_hash")
    mcp_col = cols.get("must_change_password")
    if not user_col or not pw_col or not mcp_col:
        return False, "Colunas obrigatórias ausentes na aba FUNCIONARIOS"

    col_vals = ws.col_values(user_col)
    row_idx = None
    for idx, val in enumerate(col_vals, start=1):
        if idx == 1:
            continue  # cabeçalho
        if str(val).strip().lower() == username.strip().lower():
            row_idx = idx
            break
    if not row_idx:
        return False, "Usuário não encontrado na planilha"

    ws.update_cell(row_idx, pw_col, new_hash)
    ws.update_cell(row_idx, mcp_col, "TRUE" if must_change else "FALSE")
    return True, ""

# ========= Utilidades de tempo =========
def parse_time(hhmm: str) -> dt.time:
    h, m = map(int, str(hhmm).split(":"))
    return dt.time(h, m)

def now_iso():
    return dt.datetime.now(tz.tzlocal()).isoformat(timespec="seconds")

def end_by_duration(start_dt: dt.datetime, duration_min: int) -> dt.datetime:
    return start_dt + dt.timedelta(minutes=int(duration_min))

def generate_slots(date, start="09:00", end="19:00", step_min=60):
    cur = dt.datetime.combine(date, parse_time(start))
    fim = dt.datetime.combine(date, parse_time(end))
    slots = []
    while cur < fim:
        slots.append(cur.strftime("%H:%M"))
        cur += dt.timedelta(minutes=step_min)
    return slots

# ========= Conflito de horários =========
def is_free(date, start_str, duration_min, employee_id, appts_df, blocks_df):
    start_dt = dt.datetime.combine(date, parse_time(start_str))
    end_dt = end_by_duration(start_dt, duration_min)

    ap = appts_df[
        (appts_df["employee_id"].astype(str) == str(employee_id))
        & (appts_df["date"] == date.strftime("%Y-%m-%d"))
        & (appts_df["status"].str.lower().isin(["booked", "done"]))
    ]
    for _, r in ap.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        dur = int(r.get("duration_min") or 60)
        e = end_by_duration(s, dur)
        if (start_dt < e) and (end_dt > s):
            return False

    bl = blocks_df[
        (blocks_df["employee_id"].astype(str) == str(employee_id))
        & (blocks_df["date"] == date.strftime("%Y-%m-%d"))
    ]
    for _, r in bl.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        e = dt.datetime.combine(date, parse_time(r["end_time"]))
        if (start_dt < e) and (end_dt > s):
            return False

    return True

# ========= Segurança (senha) =========
# Formato salvo em password_hash:
# pbkdf2_sha256$ITERATIONS$SALT_HEX$HASH_HEX
def hash_pw(plain: str, iterations: int = 100_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"

def check_pw(plain: str, stored: str) -> bool:
    try:
        if not stored:
            return False
        if stored.startswith("pbkdf2_sha256$"):
            alg, iters, salt_hex, hash_hex = stored.split("$", 3)
            iters = int(iters)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            test = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iters)
            return hmac.compare_digest(test, expected)
        # fallback (apenas se alguém salvou em texto)
        return plain == stored
    except Exception:
        return False

# ========= Agendar =========
def book(ws_appts, appts_df, date, time_str, duration_min, service_row, employee_row,
         cliente_nome, cliente_tel, created_by, price=None, promo_code=None, final_price=None, notes=""):
    start_dt = dt.datetime.combine(date, parse_time(time_str))
    end_dt = end_by_duration(start_dt, duration_min)
    if not is_free(date, time_str, duration_min, employee_row["employee_id"], appts_df, blocks_df):
        st.error("Esse horário acabou de ficar indisponível. Atualize a página.")
        return
    row = {
        "appt_id": f"A{int(dt.datetime.now().timestamp())}",
        "date": date.strftime("%Y-%m-%d"),
        "start_time": time_str,
        "duration_min": int(duration_min),
        "end_time": end_dt.strftime("%H:%M"),
        "employee_id": employee_row["employee_id"],
        "employee_name": employee_row["name"],
        "client_id": "",
        "client_name": cliente_nome,
        "client_phone": cliente_tel,
        "service_id": service_row.get("service_id", ""),
        "service_name": service_row.get("name", ""),
        "source_sheet": "streamlit",
        "source_row": "",
        "status": "booked",
        "created_at": now_iso(),
        "created_by": created_by,
        "notes": notes,
        "price": price or "",
        "promo_code": promo_code or "",
        "final_price": final_price or (price or ""),
    }
    append_row(ws_appts, row)
    st.success("✅ Agendamento confirmado!")

# ========= Carrega dados base =========
sh = open_sheet()
employees_df, ws_employees = read_df(sh, "FUNCIONARIOS")
services_df, ws_services = read_df(sh, "SERVICOS")
clients_df, ws_clients = read_df(sh, "CLIENTES")
appts_df, ws_appts = read_df(sh, "DB_AGENDAMENTOS")
blocks_df, ws_blocks = read_df(sh, "BLOQUEIOS")

# Normalizações
if "specialty" in employees_df.columns:
    employees_df["specialty"] = (
        employees_df["specialty"].astype(str).str.strip().str.lower()
    )

def _to_bool(x):
    return str(x).strip().lower() in ("true", "1", "sim", "yes")

if "active" in employees_df.columns and "active_bool" not in employees_df.columns:
    employees_df["active_bool"] = employees_df["active"].apply(_to_bool)
else:
    employees_df["active_bool"] = True

def ativos(df):
    return df[df["active_bool"] == True]

# ========= Login (planilha) =========
def login_view():
    st.sidebar.header("Acesso")
    u = st.sidebar.text_input("Usuário")
    p = st.sidebar.text_input("Senha", type="password")
    if st.sidebar.button("Entrar", type="primary"):
        # procura usuário
        dfu = employees_df.copy()
        if "username" not in dfu.columns:
            st.sidebar.error("Aba FUNCIONARIOS sem coluna 'username'.")
            st.stop()
        row = dfu[dfu["username"].astype(str).str.lower() == str(u).strip().lower()]
        if row.empty:
            st.sidebar.error("Usuário ou senha inválidos.")
            st.stop()
        r = row.iloc[0].to_dict()

        # senha inicial 1234 se password_hash vazio e must_change TRUE
        stored = str(r.get("password_hash") or "").strip()
        must_change = _to_bool(r.get("must_change_password"))

        # Se não há senha gravada (primeiro acesso)
        if not stored and str(p) == "1234":
            st.session_state.pending_pwd_user = r["username"]
            st.session_state.display_name = r.get("name", r["username"])
            st.session_state.role = r.get("role", "func")
            st.session_state.perfil = "admin" if str(r.get("role","")).lower()=="admin" else "func"
            st.session_state.must_change = True
            st.rerun()

        # Se já tem senha gravada, valida
        if stored and check_pw(p, stored):
            # se precisa trocar, direciona
            if must_change:
                st.session_state.pending_pwd_user = r["username"]
                st.session_state.display_name = r.get("name", r["username"])
                st.session_state.role = r.get("role", "func")
                st.session_state.perfil = "admin" if str(r.get("role","")).lower()=="admin" else "func"
                st.session_state.must_change = True
                st.rerun()
            else:
                st.session_state.auth = {
                    "usuario": r["username"],
                    "nome": r.get("name", r["username"]),
                    "perfil": "admin" if str(r.get("role","")).lower()=="admin" else "func",
                }
                st.rerun()
        else:
            st.sidebar.error("Usuário ou senha inválidos.")
            st.stop()

def change_password_view():
    st.sidebar.header("Alterar senha (obrigatório)")
    novo = st.sidebar.text_input("Nova senha", type="password")
    conf = st.sidebar.text_input("Confirmar nova senha", type="password")
    if st.sidebar.button("Salvar nova senha", type="primary"):
        if not novo or len(novo) < 4:
            st.sidebar.error("A senha deve ter pelo menos 4 caracteres.")
            st.stop()
        if novo != conf:
            st.sidebar.error("As senhas não conferem.")
            st.stop()
        h = hash_pw(novo)
        ok, msg = update_employee_password(ws_employees, st.session_state.pending_pwd_user, h, must_change=False)
        if not ok:
            st.sidebar.error(f"Falha ao salvar senha: {msg}")
            st.stop()
        st.session_state.auth = {
            "usuario": st.session_state.pending_pwd_user,
            "nome": st.session_state.display_name,
            "perfil": st.session_state.perfil,
        }
        # limpa flags
        for k in ("pending_pwd_user","display_name","role","perfil","must_change"):
            st.session_state.pop(k, None)
        st.sidebar.success("Senha atualizada!")
        st.rerun()

# Fluxo de autenticação
if "auth" not in st.session_state:
    if "pending_pwd_user" in st.session_state:
        # usuário passou no login mas precisa trocar a senha
        change_password_view()
    else:
        # tela de login
        login_view()
    st.stop()  # não renderiza nada abaixo até autenticar

auth = st.session_state["auth"]  # daqui pra baixo temos certeza que existe
st.sidebar.success(f"Olá, {auth['nome']} ({auth['perfil']})")
if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.rerun()


# ========= UI principal =========
aba_agendar, aba_func, aba_admin, aba_dash = st.tabs(
    ["📅 Agendar (Cliente)", "🧑‍🔧 Funcionário", "🛠️ Admin", "📈 Dashboard"]
)

with aba_agendar:
    st.subheader("Agendar atendimento")
    col1, col2, col3 = st.columns(3)

    with col1:
        data_sel = st.date_input("Data", dt.date.today())
        esp_ops = sorted(services_df["specialty"].dropna().astype(str).str.lower().unique())
        esp = st.selectbox("Especialidade", esp_ops)
        svc_ops = (
            services_df[
                (services_df["specialty"].astype(str).str.lower() == esp)
                & (services_df["active"].astype(str).str.upper() != "FALSE")
            ]["name"]
            .dropna()
            .astype(str)
            .tolist()
        )
        if not svc_ops:
            st.warning("Nenhum serviço ativo para essa especialidade.")
            st.stop()
        svc = st.selectbox("Serviço", svc_ops)
        svc_row = services_df[services_df["name"] == svc].iloc[0].to_dict()

    with col2:
        profs = ativos(employees_df)
        profs = profs[profs["specialty"] == esp]
        if profs.empty:
            st.warning("Nenhum profissional **ativo** para essa especialidade.")
            st.stop()
        nomes = profs["name"].dropna().astype(str).tolist()
        prof_nome = st.selectbox("Profissional", nomes)
        linha = profs.loc[profs["name"] == prof_nome]
        if linha.empty:
            st.error("Profissional não encontrado após filtro.")
            st.stop()
        prof_row = linha.iloc[0].to_dict()

        start_def = prof_row.get("default_start", "09:00")
        end_def = prof_row.get("default_end", "19:00")
        slots = generate_slots(data_sel, start_def, end_def, step_min=60)
        hora = st.selectbox("Horário", slots)

    with col3:
        cli_nome = st.text_input("Seu nome")
        cli_tel = st.text_input("Telefone")
        if st.button("Confirmar agendamento", type="primary"):
            if not cli_nome:
                st.error("Informe o nome.")
            else:
                book(
                    ws_appts,
                    appts_df,
                    data_sel,
                    hora,
                    duration_min=int(svc_row.get("default_duration_min") or 60),
                    service_row=svc_row,
                    employee_row=prof_row,
                    cliente_nome=cli_nome,
                    cliente_tel=cli_tel,
                    created_by=auth["usuario"],
                )

with aba_func:
    st.subheader("Área do Funcionário")
    if auth["perfil"] not in ["func", "admin"]:
        st.info("Acesse com perfil de Funcionário para usar esta aba.")
    else:
        minhas = ativos(employees_df)
        if auth["perfil"] == "func":
            minhas = minhas[minhas["name"].astype(str).str.upper() == auth["nome"].upper()]
        if minhas.empty:
            st.warning("Nenhum profissional ativo encontrado.")
            st.stop()

        nome2 = st.selectbox("Profissional", minhas["name"].dropna().astype(str).tolist())
        emp2 = minhas[minhas["name"] == nome2].iloc[0].to_dict()
        data2 = st.date_input("Data", dt.date.today(), key="d2")

        st.markdown("### Bloquear horário")
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            ini_b = st.text_input("Início (HH:MM)", "13:00")
        with b2:
            fim_b = st.text_input("Fim (HH:MM)", "15:00")
        with b3:
            motivo = st.text_input("Motivo", "Sem atendimento")
        with b4:
            if st.button("Bloquear período"):
                row = {
                    "block_id": f"B{int(dt.datetime.now().timestamp())}",
                    "date": data2.strftime("%Y-%m-%d"),
                    "start_time": ini_b,
                    "end_time": fim_b,
                    "employee_id": emp2["employee_id"],
                    "employee_name": emp2["name"],
                    "reason": motivo,
                    "created_at": now_iso(),
                    "created_by": auth["usuario"],
                }
                append_row(ws_blocks, row)
                st.success("Período bloqueado.")

        st.divider()
        st.markdown("### Agendar com duração customizada (override)")
        svc2 = st.selectbox(
            "Serviço",
            services_df[
                services_df["specialty"].astype(str).str.lower() == str(emp2["specialty"]).lower()
            ]["name"].dropna().astype(str).tolist(),
            key="svc2",
        )
        svc2_row = services_df[services_df["name"] == svc2].iloc[0].to_dict()
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            hora_livre = st.text_input("Horário (HH:MM)", "19:30")
        with c2:
            dur = st.number_input(
                "Duração (min)", min_value=15, max_value=240, step=15,
                value=int(svc2_row.get("default_duration_min") or 60)
            )
        with c3:
            preco = st.text_input("Preço R$", "")
        with c4:
            promo = st.text_input("Promoção (código)", "")
        cli2 = st.text_input("Nome do cliente", key="cli2")
        tel2 = st.text_input("Telefone", key="tel2")
        obs2 = st.text_input("Observações", key="obs2")
        if st.button("Agendar override"):
            book(
                ws_appts, appts_df, data2, hora_livre, dur, svc2_row, emp2,
                cli2, tel2, created_by=auth["usuario"], price=preco,
                promo_code=promo, final_price=preco, notes=obs2
            )

with aba_admin:
    st.subheader("Administração")
    if auth["perfil"] != "admin":
        st.info("Acesso restrito.")
    else:
        st.write("Cadastre/edite **serviços, funcionários e clientes** diretamente na planilha.")
        st.caption("Duração padrão do serviço define o slot de cliente (60 min usual).")

with aba_dash:
    st.subheader("Resumo do dia")
    hoje = dt.date.today().strftime("%Y-%m-%d")
    day = appts_df[appts_df["date"] == hoje]
    st.metric("Atendimentos hoje", len(day))
    colA, colB = st.columns(2)
    with colA:
        if not day.empty:
            por_prof = day.groupby("employee_id").size().reset_index(name="qtd")
        else:
            por_prof = pd.DataFrame({"employee_id": [], "qtd": []})
        st.write("Por profissional")
        st.dataframe(por_prof, use_container_width=True)
    with colB:
        if not day.empty:
            serv = (
                day.groupby("service_name")
                .size()
                .reset_index(name="qtd")
                .sort_values("qtd", ascending=False)
            )
        else:
            serv = pd.DataFrame({"service_name": [], "qtd": []})
        st.write("Serviços do dia")
        st.dataframe(serv, use_container_width=True)
