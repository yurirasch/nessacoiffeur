import streamlit as st
import pandas as pd
import datetime as dt
from dateutil import tz
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Nessa Coiffeur - Agenda", layout="wide")

USERS = {
    "admin":     {"nome":"Admin",   "senha":"admin123", "perfil":"admin"},
    "luciene":   {"nome":"LUCIENE", "senha":"luc123",   "perfil":"func"},
    "marcela":   {"nome":"MARCELA", "senha":"mar123",   "perfil":"func"},
    "tina":      {"nome":"TINA",    "senha":"tina123",  "perfil":"func"},
    "cliente":   {"nome":"Cliente", "senha":"cli123",   "perfil":"cliente"},
}

def gs_client():
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        st.secrets["gcp_service_account"], scope)
    return gspread.authorize(creds)

@st.cache_data(ttl=30)
def open_sheet():
    cli = gs_client()
    sh = cli.open_by_key(st.secrets["sheet_id"])
    return sh

def read_df(sh, tab):
    ws = sh.worksheet(tab)
    df = pd.DataFrame(ws.get_all_records())
    return df, ws

def append_row(ws, d):
    headers = ws.row_values(1)
    row = [d.get(h,"") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")

def parse_time(hhmm:str)->dt.time:
    h,m = map(int, str(hhmm).split(":"))
    return dt.time(h,m)

def now_iso():
    return dt.datetime.now(tz.tzlocal()).isoformat(timespec="seconds")

def end_by_duration(start_dt, duration_min:int)->dt.datetime:
    return start_dt + dt.timedelta(minutes=int(duration_min))

def generate_slots(date, start="09:00", end="19:00", step_min=60):
    cur = dt.datetime.combine(date, parse_time(start))
    fim = dt.datetime.combine(date, parse_time(end))
    slots=[]
    while cur < fim:
        slots.append(cur.strftime("%H:%M"))
        cur += dt.timedelta(minutes=step_min)
    return slots

def is_free(date, start_str, duration_min, employee_id, appts_df, blocks_df):
    start_dt = dt.datetime.combine(date, parse_time(start_str))
    end_dt   = end_by_duration(start_dt, duration_min)

    # Conflito com agendamentos
    ap = appts_df[
        (appts_df["employee_id"].astype(str)==str(employee_id)) &
        (appts_df["date"]==date.strftime("%Y-%m-%d")) &
        (appts_df["status"].str.lower().isin(["booked","done"]))
    ]
    for _,r in ap.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        dur = int(r.get("duration_min") or 60)
        e = end_by_duration(s, dur)
        if (start_dt < e) and (end_dt > s):
            return False

    # Conflito com bloqueios
    bl = blocks_df[
        (blocks_df["employee_id"].astype(str)==str(employee_id)) &
        (blocks_df["date"]==date.strftime("%Y-%m-%d"))
    ]
    for _,r in bl.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        e = dt.datetime.combine(date, parse_time(r["end_time"]))
        if (start_dt < e) and (end_dt > s):
            return False

    return True

def book(ws_appts, appts_df, date, time_str, duration_min, service_row,
         employee_row, cliente_nome, cliente_tel, created_by,
         price=None, promo_code=None, final_price=None, notes=""):
    start_dt = dt.datetime.combine(date, parse_time(time_str))
    end_dt   = end_by_duration(start_dt, duration_min)

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
        "service_id": service_row.get("service_id",""),
        "service_name": service_row.get("name",""),
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
    st.success("Agendamento confirmado!")

# ------------------ LOGIN ------------------
def do_login():
    st.sidebar.header("Acesso")
    u = st.sidebar.text_input("Usuário")
    p = st.sidebar.text_input("Senha", type="password")
    if st.sidebar.button("Entrar"):
        info = USERS.get(u)
        if info and p == info["senha"]:
            st.session_state.auth = {"usuario":u, "nome":info["nome"], "perfil":info["perfil"]}
            st.rerun()
        else:
            st.sidebar.error("Usuário ou senha inválidos.")
    st.stop()

if "auth" not in st.session_state:
    do_login()

auth = st.session_state.auth
st.sidebar.success(f"Olá, {auth['nome']} ({auth['perfil']})")
if st.sidebar.button("Sair"):
    st.session_state.pop("auth")
    st.rerun()

# ------------------ DADOS ------------------
sh = open_sheet()
employees_df, ws_employees = read_df(sh, "FUNCIONARIOS")
services_df,  ws_services  = read_df(sh, "SERVICOS")
clients_df,   ws_clients   = read_df(sh, "CLIENTES")
appts_df,     ws_appts     = read_df(sh, "DB_AGENDAMENTOS")
blocks_df,    ws_blocks    = read_df(sh, "BLOQUEIOS")

def ativos(df):
    if "active" in df.columns:
        return df[df["active"].astype(str).str.upper().isin(["TRUE","1","YES"])]
    return df

# ------------------ UI ------------------
aba_agendar, aba_func, aba_admin, aba_dash = st.tabs(
    ["📅 Agendar (Cliente)", "🧑‍🔧 Funcionário", "🛠️ Admin", "📈 Dashboard"]
)

with aba_agendar:
    st.subheader("Agendar atendimento")
    col1,col2,col3 = st.columns(3)

    with col1:
        data_sel = st.date_input("Data", dt.date.today())
        esp_ops = sorted(services_df["specialty"].dropna().unique())
        esp = st.selectbox("Especialidade", esp_ops)
        svc_ops = services_df[(services_df["specialty"]==esp) & (services_df["active"].astype(str).str.upper()!="FALSE")]["name"].tolist()
        svc = st.selectbox("Serviço", svc_ops)
        svc_row = services_df[services_df["name"]==svc].iloc[0].to_dict()

    with col2:
        profs = ativos(employees_df)
        profs = profs[profs["specialty"]==esp]
        prof_nome = st.selectbox("Profissional", profs["name"].tolist())
        prof_row = profs[profs["name"]==prof_nome].iloc[0].to_dict()
        start_def = prof_row.get("default_start","09:00")
        end_def   = prof_row.get("default_end","19:00")
        slots = generate_slots(data_sel, start_def, end_def, step_min=60)  # cliente = 60 min
        hora = st.selectbox("Horário", slots)

    with col3:
        cli_nome = st.text_input("Seu nome")
        cli_tel  = st.text_input("Telefone")
        if st.button("Confirmar agendamento"):
            if not cli_nome:
                st.error("Informe o nome.")
            else:
                book(ws_appts, appts_df, data_sel, hora,
                     duration_min=int(svc_row.get("default_duration_min") or 60),
                     service_row=svc_row, employee_row=prof_row,
                     cliente_nome=cli_nome, cliente_tel=cli_tel,
                     created_by=auth["usuario"])

with aba_func:
    st.subheader("Área do Funcionário")
    if auth["perfil"] not in ["func","admin"]:
        st.info("Acesse com perfil de Funcionário para usar esta aba.")
    else:
        minhas = ativos(employees_df)
        if auth["perfil"]=="func":
            minhas = minhas[minhas["name"].str.upper()==auth["nome"].upper()]
        nome2 = st.selectbox("Profissional", minhas["name"].tolist())
        emp2  = minhas[minhas["name"]==nome2].iloc[0].to_dict()
        data2 = st.date_input("Data", dt.date.today(), key="d2")

        st.markdown("### Bloquear horário")
        b1,b2,b3,b4 = st.columns(4)
        with b1: ini_b = st.text_input("Início (HH:MM)", "13:00")
        with b2: fim_b = st.text_input("Fim (HH:MM)", "15:00")
        with b3: motivo = st.text_input("Motivo", "Sem atendimento")
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
                    "created_by": auth["usuario"]
                }
                append_row(ws_blocks, row)
                st.success("Período bloqueado.")

        st.divider()
        st.markdown("### Agendar com duração customizada (override)")
        svc2 = st.selectbox("Serviço", services_df[services_df["specialty"]==emp2["specialty"]]["name"].tolist(), key="svc2")
        svc2_row = services_df[services_df["name"]==svc2].iloc[0].to_dict()
        c1,c2,c3,c4 = st.columns(4)
        with c1: hora_livre = st.text_input("Horário (HH:MM)", "19:30")
        with c2: dur = st.number_input("Duração (min)", min_value=15, max_value=240, step=15, value=int(svc2_row.get("default_duration_min") or 60))
        with c3: preco = st.text_input("Preço R$", "")
        with c4: promo = st.text_input("Promoção (código)", "")
        cli2 = st.text_input("Nome do cliente", key="cli2")
        tel2 = st.text_input("Telefone", key="tel2")
        obs2 = st.text_input("Observações", key="obs2")
        if st.button("Agendar override"):
            book(ws_appts, appts_df, data2, hora_livre, dur, svc2_row, emp2, cli2, tel2,
                 created_by=auth["usuario"], price=preco, promo_code=promo, final_price=preco, notes=obs2)

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
    day = appts_df[appts_df["date"]==hoje]
    st.metric("Atendimentos hoje", len(day))
    colA, colB = st.columns(2)
    with colA:
        por_prof = day.groupby("employee_id").size().reset_index(name="qtd")
        st.write("Por profissional")
        st.dataframe(por_prof)
    with colB:
        serv = day.groupby("service_name").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
        st.write("Serviços do dia")
        st.dataframe(serv)
