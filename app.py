# app.py — Nessa Coiffeur (PT-BR) — Backend: JSON no GitHub
import os
import hmac
import hashlib
import base64
import json
import time
from functools import wraps

import streamlit as st
import pandas as pd
import datetime as dt
from dateutil import tz
import requests

st.set_page_config(page_title="Nessa Coiffeur - Agenda", layout="wide")
st.set_option("client.showErrorDetails", True)

# =========================
# Config via Secrets
# =========================
GH_TOKEN  = st.secrets["GITHUB_TOKEN"]             # obrigatório
GH_REPO   = st.secrets.get("GH_REPO", "yurirasch/nessacoiffeur")
GH_BRANCH = st.secrets.get("GH_BRANCH", "main")
DB_PATH   = st.secrets.get("DB_PATH", "db/db.json")



API_BASE = "https://api.github.com"

# =========================
# Helpers GitHub API
# =========================
def gh_headers():
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

def with_backoff(func):
    @wraps(func)
    def _wrap(*a, **kw):
        delay = 0.7
        for i in range(5):
            try:
                return func(*a, **kw)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                # backoff para 403/429/5xx
                if status in (403, 429) or (status and 500 <= status < 600):
                    time.sleep(delay)
                    delay = min(delay * 1.8, 6)
                    continue
                raise
            except Exception:
                raise
        return func(*a, **kw)
    return _wrap

@with_backoff
def gh_get_file(repo: str, path: str, ref: str):
    # GET /repos/{owner}/{repo}/contents/{path}?ref=branch
    url = f"{API_BASE}/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=gh_headers(), params={"ref": ref})
    if r.status_code == 404:
        # arquivo pode não existir (primeiro deploy)
        return {"sha": None, "content": None}
    r.raise_for_status()
    data = r.json()
    # content vem em base64
    content_b64 = data.get("content", "")
    decoded = base64.b64decode(content_b64).decode("utf-8") if content_b64 else ""
    return {"sha": data.get("sha"), "content": decoded}

@with_backoff
def gh_put_file(repo: str, path: str, ref: str, content_str: str, sha: str | None, message: str):
    # PUT /repos/{owner}/{repo}/contents/{path}
    url = f"{API_BASE}/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        "branch": ref,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=gh_headers(), json=payload)
    r.raise_for_status()
    return r.json()

# =========================
# Banco (DB) no JSON
# =========================
@st.cache_data(ttl=60, show_spinner=False)
def load_db():
    res = gh_get_file(GH_REPO, DB_PATH, GH_BRANCH)
    text = res["content"] or ""
    sha  = res["sha"]
    if not text:
        # seed vazio
        db = {
            "clientes": [],
            "servicos": [],
            "funcionarios": [],
            "agendamentos": [],
            "bloqueios": [],
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        }
    else:
        db = json.loads(text)
    return db, sha

def save_db(db: dict, old_sha: str | None, msg: str):
    # invalida cache antes de escrever (evita race de leitura)
    st.cache_data.clear()
    return gh_put_file(GH_REPO, DB_PATH, GH_BRANCH, json.dumps(db, ensure_ascii=False, indent=2), old_sha, msg)

# =========================
# Utilidades
# =========================
def now_iso():
    return dt.datetime.now(tz.tzlocal()).isoformat(timespec="seconds")

def parse_time(hhmm: str) -> dt.time:
    h, m = map(int, str(hhmm).split(":"))
    return dt.time(h, m)

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

def _to_bool(x):
    return str(x).strip().lower() in ("true", "1", "sim", "yes")

# Segurança de senha (PBKDF2)
def hash_pw(plain: str, iterations: int = 100_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"

def check_pw(plain: str, stored: str) -> bool:
    try:
        if not stored:
            return False
        if stored.startswith("pbkdf2_sha256$"):
            _, iters, salt_hex, hash_hex = stored.split("$", 3)
            iters = int(iters)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            test = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iters)
            return hmac.compare_digest(test, expected)
        return plain == stored
    except Exception:
        return False

# =========================
# Carregar DB
# =========================
try:
    DB, DB_SHA = load_db()
except Exception as e:
    st.error(f"❌ Falha ao carregar DB do GitHub: {e}")
    st.stop()

# Transformar listas do DB em DataFrames
employees_df = pd.DataFrame(DB.get("funcionarios", []))
services_df  = pd.DataFrame(DB.get("servicos", []))
clients_df   = pd.DataFrame(DB.get("clientes", []))
appts_df     = pd.DataFrame(DB.get("agendamentos", []))
blocks_df    = pd.DataFrame(DB.get("bloqueios", []))

# Normalizações
def ensure_cols(df, cols_defaults: dict):
    for k, v in cols_defaults.items():
        if k not in df.columns:
            df[k] = v
    return df

employees_df = ensure_cols(employees_df, {
    "employee_id": "", "name": "", "role": "", "specialty": "", "active": True,
    "default_start": "09:00", "default_end": "19:00", "username": "", "email": "",
    "password_hash": "", "must_change_password": True
})
services_df = ensure_cols(services_df, {
    "service_id": "", "name": "", "specialty": "", "active": True,
    "default_duration": 60
})
appts_df = ensure_cols(appts_df, {
    "appt_id": "", "date": "", "start_time": "", "duration_min": 60, "end_time": "",
    "employee_id": "", "employee_name": "", "client_id": "", "client_name": "",
    "client_phone": "", "service_id": "", "service_name": "", "source_sheet": "streamlit",
    "source_row": "", "status": "booked", "created_at": "", "created_by": "",
    "notes": "", "price": "", "promo_code": "", "final_price": ""
})
blocks_df = ensure_cols(blocks_df, {
    "block_id": "", "date": "", "start_time": "", "end_time": "",
    "employee_id": "", "employee_name": "", "reason": "", "created_at": "", "created_by": ""
})

# Colunas normalizadas
for df in (services_df, employees_df):
    for col in ("name", "specialty"):
        df[col] = df[col].astype(str).str.strip()
        df[f"{col}_norm"] = df[col].str.upper()

if "active" in employees_df.columns:
    employees_df["active_bool"] = employees_df["active"].apply(_to_bool)
else:
    employees_df["active_bool"] = True
if "active" in services_df.columns:
    services_df["active_bool"] = services_df["active"].apply(_to_bool)
else:
    services_df["active_bool"] = True

def ativos(df):
    return df[df["active_bool"] == True]

def service_duration_min(svc_row: dict) -> int:
    for k in ("default_duration_min", "default_duration", "duration_min"):
        if k in svc_row and str(svc_row[k]).strip() != "":
            try:
                return int(svc_row[k])
            except:
                pass
    return 60

def get_service_row(name_sel: str):
    if not name_sel:
        return None
    df = services_df[services_df["name"].astype(str).str.strip().str.upper() == str(name_sel).strip().upper()]
    if df.empty:
        return None
    return df.iloc[0].to_dict()

# =========================
# Escritas no DB (GitHub)
# =========================
def db_append_and_save(kind: str, row: dict, msg: str):
    """kind: 'agendamentos' | 'bloqueios' | 'funcionarios' | 'clientes'"""
    global DB, DB_SHA
    DB[kind].append(row)
    res = save_db(DB, DB_SHA, msg)
    # atualizar SHA local
    DB_SHA = res.get("content", {}).get("sha", DB_SHA)
    st.cache_data.clear()

def db_update_employee_password(username: str, new_hash: str, must_change=False):
    global DB, DB_SHA
    for emp in DB["funcionarios"]:
        if str(emp.get("username", "")).strip().lower() == username.strip().lower():
            emp["password_hash"] = new_hash
            emp["must_change_password"] = bool(must_change)
            break
    else:
        return False, "Usuário não encontrado"
    res = save_db(DB, DB_SHA, f"feat: update password for {username}")
    DB_SHA = res.get("content", {}).get("sha", DB_SHA)
    st.cache_data.clear()
    return True, ""

# =========================
# Login / Troca de senha
# =========================
def login_view():
    st.sidebar.header("Acesso")
    with st.sidebar.form("login_form"):
        u = st.text_input("Usuário")
        p = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar", type="primary")

    if ok:
        if "username" not in employees_df.columns:
            st.sidebar.error("Base de funcionários sem coluna 'username'.")
            st.stop()
        mask = employees_df["username"].astype(str).str.strip().str.lower() == str(u).strip().lower()
        row = employees_df[mask]
        if row.empty:
            st.sidebar.error("Usuário ou senha inválidos.")
            st.stop()
        r = row.iloc[0].to_dict()
        stored = str(r.get("password_hash") or "").strip()
        must_change = _to_bool(r.get("must_change_password"))

        # Primeiro acesso
        if stored == "":
            if p == "1234" or p == "":
                st.session_state.pending_pwd_user = r["username"]
                st.session_state.display_name = r.get("name", r["username"])
                st.session_state.perfil = "admin" if str(r.get("role","")).lower()=="admin" else "func"
                st.session_state.must_change = True
                st.rerun()
            else:
                st.sidebar.error("Para primeiro acesso use a senha 1234 (ou deixe em branco).")
                st.stop()

        # Login normal
        if stored and check_pw(p, stored):
            if must_change:
                st.session_state.pending_pwd_user = r["username"]
                st.session_state.display_name = r.get("name", r["username"])
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
    with st.sidebar.form("pwd_form"):
        novo = st.text_input("Nova senha", type="password")
        conf = st.text_input("Confirmar nova senha", type="password")
        ok = st.form_submit_button("Salvar nova senha", type="primary")
    if ok:
        if not novo or len(novo) < 4:
            st.sidebar.error("A senha deve ter pelo menos 4 caracteres.")
            st.stop()
        if novo != conf:
            st.sidebar.error("As senhas não conferem.")
            st.stop()
        h = hash_pw(novo)
        ok2, msg = db_update_employee_password(st.session_state.pending_pwd_user, h, must_change=False)
        if not ok2:
            st.sidebar.error(f"Falha ao salvar senha: {msg}")
            st.stop()
        st.session_state.auth = {
            "usuario": st.session_state.pending_pwd_user,
            "nome": st.session_state.display_name,
            "perfil": st.session_state.perfil,
        }
        for k in ("pending_pwd_user","display_name","perfil","must_change"):
            st.session_state.pop(k, None)
        st.sidebar.success("Senha atualizada!")
        st.rerun()

# Gate de autenticação
if "auth" not in st.session_state:
    if "pending_pwd_user" in st.session_state:
        change_password_view()
    else:
        login_view()
    st.stop()

auth = st.session_state["auth"]
st.sidebar.success(f"Olá, {auth['nome']} ({auth['perfil']})")
if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.rerun()

# =========================
# Regras de negócio
# =========================
def is_free(date, start_str, duration_min, employee_id, appts_df, blocks_df):
    start_dt = dt.datetime.combine(date, parse_time(start_str))
    end_dt = end_by_duration(start_dt, duration_min)

    ap = appts_df[
        (appts_df["employee_id"].astype(str) == str(employee_id)) &
        (appts_df["date"] == date.strftime("%Y-%m-%d")) &
        (appts_df["status"].astype(str).str.lower().isin(["booked","done"]))
    ]
    for _, r in ap.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        dur = int(r.get("duration_min") or 60)
        e = end_by_duration(s, dur)
        if (start_dt < e) and (end_dt > s):
            return False

    bl = blocks_df[
        (blocks_df["employee_id"].astype(str) == str(employee_id)) &
        (blocks_df["date"] == date.strftime("%Y-%m-%d"))
    ]
    for _, r in bl.iterrows():
        s = dt.datetime.combine(date, parse_time(r["start_time"]))
        e = dt.datetime.combine(date, parse_time(r["end_time"]))
        if (start_dt < e) and (end_dt > s):
            return False
    return True

def book_appointment(date, time_str, duration_min, service_row, employee_row,
                     cliente_nome, cliente_tel, created_by, price=None, promo_code=None,
                     final_price=None, notes=""):
    end_dt = end_by_duration(dt.datetime.combine(date, parse_time(time_str)), duration_min)
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
        "notes": notes or "",
        "price": price or "",
        "promo_code": promo_code or "",
        "final_price": final_price or (price or "")
    }
    db_append_and_save("agendamentos", row, f"feat: novo agendamento {row['appt_id']}")

def block_period(date, start_str, end_str, employee_row, reason, created_by):
    row = {
        "block_id": f"B{int(dt.datetime.now().timestamp())}",
        "date": date.strftime("%Y-%m-%d"),
        "start_time": start_str,
        "end_time": end_str,
        "employee_id": employee_row["employee_id"],
        "employee_name": employee_row["name"],
        "reason": reason or "Sem atendimento",
        "created_at": now_iso(),
        "created_by": created_by
    }
    db_append_and_save("bloqueios", row, f"feat: novo bloqueio {row['block_id']}")

# =========================
# UI principal
# =========================
st.title("Nessa Coiffeur — Agenda")

# Botão de refresh manual (renova cache e relê JSON)
if st.button("🔄 Atualizar dados agora"):
    st.cache_data.clear()
    st.experimental_rerun()

aba_agendar, aba_func, aba_admin, aba_dash = st.tabs(
    ["📅 Agendar (Cliente)", "🧑‍🔧 Funcionário", "🛠️ Admin", "📈 Dashboard"]
)

with aba_agendar:
    st.subheader("Agendar atendimento")
    with st.form("form_agendar_cliente", clear_on_submit=False):
        col1, col2, col3 = st.columns(3)

        with col1:
            data_sel = st.date_input("Data", dt.date.today())

            esp_ops = sorted(services_df["specialty"].dropna().unique().tolist())
            esp = st.selectbox("Especialidade", esp_ops)

            svc_ops = services_df[
                (services_df["specialty"].astype(str).str.strip().str.upper() == str(esp).strip().upper()) &
                (services_df["active_bool"])
            ]["name"].dropna().unique().tolist()
            svc = st.selectbox("Serviço", svc_ops)

        with col2:
            profs = ativos(employees_df)
            profs = profs[profs["specialty"].astype(str).str.strip().str.upper() == str(esp).strip().upper()]
            prof_nome = st.selectbox("Profissional", profs["name"].tolist() if not profs.empty else [])
            if not profs.empty:
                prof_row = profs[profs["name"] == prof_nome].iloc[0].to_dict()
                start_def = prof_row.get("default_start", "09:00")
                end_def   = prof_row.get("default_end", "19:00")
                slots = generate_slots(data_sel, start_def, end_def, step_min=60)
                hora = st.selectbox("Horário", slots)
            else:
                prof_row, hora = None, None

        with col3:
            cli_nome = st.text_input("Seu nome")
            cli_tel  = st.text_input("Telefone")
            enviar = st.form_submit_button("Confirmar agendamento", type="primary")

        if enviar:
            if not (esp and svc and prof_row and hora and cli_nome):
                st.error("Preencha todos os campos.")
            else:
                svc_row = get_service_row(svc)
                if not svc_row:
                    st.error("Serviço não encontrado.")
                else:
                    dur = service_duration_min(svc_row)
                    if not is_free(data_sel, hora, dur, prof_row["employee_id"], appts_df, blocks_df):
                        st.error("Esse horário está ocupado/bloqueado.")
                    else:
                        book_appointment(
                            data_sel, hora, dur, svc_row, prof_row,
                            cliente_nome=cli_nome, cliente_tel=cli_tel, created_by=auth["usuario"]
                        )
                        st.success("✅ Agendamento confirmado!")
                        st.cache_data.clear()
                        st.experimental_rerun()

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
        else:
            with st.form("form_func"):
                nome2 = st.selectbox("Profissional", minhas["name"].tolist())
                emp2  = minhas[minhas["name"] == nome2].iloc[0].to_dict()
                data2 = st.date_input("Data", dt.date.today(), key="d2")

                st.markdown("### Bloquear horário")
                b1, b2, b3 = st.columns(3)
                with b1: ini_b = st.text_input("Início (HH:MM)", "13:00")
                with b2: fim_b = st.text_input("Fim (HH:MM)", "15:00")
                with b3: motivo = st.text_input("Motivo", "Sem atendimento")

                st.markdown("### Agendar com duração customizada (override)")
                svc2_ops = services_df[
                    services_df["specialty"].astype(str).str.strip().str.upper() == str(emp2["specialty"]).strip().upper()
                ]["name"].dropna().unique().tolist()
                svc2 = st.selectbox("Serviço", svc2_ops, key="svc2")

                svc2_row = get_service_row(svc2) if svc2 else None
                c1, c2, c3, c4 = st.columns(4)
                with c1: hora_livre = st.text_input("Horário (HH:MM)", "19:30")
                with c2: dur = st.number_input("Duração (min)", min_value=15, max_value=240, step=15,
                                               value=service_duration_min(svc2_row or {}))
                with c3: preco = st.text_input("Preço R$", "")
                with c4: promo = st.text_input("Promoção (código)", "")
                cli2 = st.text_input("Nome do cliente", key="cli2")
                tel2 = st.text_input("Telefone", key="tel2")
                obs2 = st.text_input("Observações", key="obs2")

                colx, coly = st.columns(2)
                with colx:
                    bt_block = st.form_submit_button("Bloquear período")
                with coly:
                    bt_ag = st.form_submit_button("Agendar override", type="primary")

            if bt_block:
                block_period(data2, ini_b, fim_b, emp2, motivo, auth["usuario"])
                st.success("Período bloqueado.")
                st.cache_data.clear()
                st.experimental_rerun()

            if bt_ag:
                if not svc2_row:
                    st.error("Selecione um serviço válido.")
                elif not is_free(data2, hora_livre, int(dur), emp2["employee_id"], appts_df, blocks_df):
                    st.error("Esse horário está ocupado/bloqueado.")
                else:
                    book_appointment(
                        data2, hora_livre, int(dur), svc2_row, emp2,
                        cliente_nome=cli2, cliente_tel=tel2, created_by=auth["usuario"],
                        price=preco, promo_code=promo, final_price=preco, notes=obs2
                    )
                    st.success("Agendamento criado.")
                    st.cache_data.clear()
                    st.experimental_rerun()

with aba_admin:
    st.subheader("Administração")
    if auth["perfil"] != "admin":
        st.info("Acesso restrito.")
    else:
        st.write("Cadastre/edite **serviços, funcionários e clientes** no arquivo JSON do repositório.")
        st.caption("Dica: mantenha `default_duration` dos serviços (minutos).")

with aba_dash:
    st.subheader("Resumo do dia")
    hoje = dt.date.today().strftime("%Y-%m-%d")
    day = appts_df[appts_df["date"] == hoje] if not appts_df.empty else pd.DataFrame([])
    st.metric("Atendimentos hoje", len(day))
    colA, colB = st.columns(2)
    with colA:
        por_prof = (
            day.groupby("employee_id").size().reset_index(name="qtd")
            if not day.empty else pd.DataFrame({"employee_id": [], "qtd": []})
        )
        st.write("Por profissional")
        st.dataframe(por_prof, use_container_width=True)
    with colB:
        serv = (
            day.groupby("service_name").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
            if not day.empty else pd.DataFrame({"service_name": [], "qtd": []})
        )
        st.write("Serviços do dia")
        st.dataframe(serv, use_container_width=True)
