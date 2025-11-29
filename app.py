import streamlit as st
import sqlite3
import pandas as pd
import folium
from folium.plugins import MarkerCluster
import datetime
import os
import html
import sys
from typing import Optional

# Hashing
from passlib.hash import pbkdf2_sha256

# --- 0. CONFIG ---
DB_NAME = "home_care_v21.db"

# --- CLEAN INSTALL (come nel codice originale) ---
if os.path.exists(DB_NAME):
    try:
        os.remove(DB_NAME)
        print(f"🗑️ Vecchio database {DB_NAME} eliminato per pulizia.")
    except Exception as e:
        print("Impossibile eliminare DB:", e)

# --- AI (opzionale) ---
AI_AVAILABLE = False
model = None
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
    model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    AI_AVAILABLE = True
    print("AI caricata.")
except Exception:
    AI_AVAILABLE = False

# --- COSTANTI E DATI ---
INTERVENTION_MAPPING = {
    "Assistenza Infermieristica": ["Infermiere"],
    "Riabilitazione Motoria / Fisioterapia": ["Fisioterapista"],
    "Igiene e Cura Personale": ["OSS", "Badante"],
    "Supporto Notturno": ["OSS", "Badante"],
    "Preparazione Pasti e Spesa": ["Badante", "OSA"],
    "Visita Medica": ["Medico"],
    "Supporto Psicologico": ["Psicologo"]
}

KNOWLEDGE_BASE = list(INTERVENTION_MAPPING.keys())
ALL_QUALIFICATIONS = list(set([q for sublist in INTERVENTION_MAPPING.values() for q in sublist]))

CITY_COORDS = {
    "Milano": (45.4642, 9.1900), "Roma": (41.9028, 12.4964), "Napoli": (40.8518, 14.2681),
    "Torino": (45.0703, 7.6869), "Firenze": (43.7696, 11.2558), "Bologna": (44.4949, 11.3426),
    "Palermo": (38.1157, 13.3615), "Bari": (41.1171, 16.8719)
}

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE, password TEXT, role TEXT, city TEXT,
                  lat REAL, lon REAL, bio TEXT, qualification TEXT,
                  experience INTEGER, hourly_rate REAL,
                  email TEXT, address TEXT, age INTEGER,
                  clinical_history TEXT, detailed_experience TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS requests
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  patient_id INTEGER,
                  professional_id INTEGER,
                  target_pro_id INTEGER,
                  intervention_type TEXT,
                  description TEXT,
                  city TEXT,
                  status TEXT,
                  created_at TEXT,
                  FOREIGN KEY(patient_id) REFERENCES users(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  request_id INTEGER,
                  sender_id INTEGER,
                  content TEXT,
                  timestamp TEXT,
                  FOREIGN KEY(request_id) REFERENCES requests(id))''')
    conn.commit()
    conn.close()
    print("✅ Database creato ex-novo.")

def seed_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if c.execute("SELECT count(*) FROM users").fetchone()[0] == 0:
        print("🌱 Inserimento dati demo...")
        # Hashed demo password
        hashed_pass = pbkdf2_sha256.hash("pass")
        users = [
            # Paziente: mario_rossi
            ("mario_rossi", hashed_pass, "paziente", "Milano", 45.4642, 9.1900, "Paziente Demo", None, 0, 0, "mario@email.it", "Via Roma 1", 80, "Diabete", None),
            # Professionista: luigi_verdi
            ("luigi_verdi", hashed_pass, "professionista", "Milano", 45.4680, 9.2000, "Infermiere Pro", "Infermiere", 10, 25.0, "luigi@nurse.it", "Via Milano 20", 40, None, "Exp 10 anni")
        ]
        c.executemany("INSERT INTO users VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", users)
        conn.commit()
    conn.close()

init_db()
seed_data()

# --- UTILITY DB HELPERS ---
def conn_fetch_user_by_username(username: str):
    conn = sqlite3.connect(DB_NAME)
    u = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return u

def debug_show_hash(username: str):
    u = conn_fetch_user_by_username(username)
    if not u:
        return f"Utente '{username}' non trovato"
    return f"id={u[0]}, username={u[1]}, stored_hash_present={bool(u[2])}\nhash={u[2]!s}"

# --- BACKEND LOGIC (stessa logica adattata) ---
def authenticate(usr, pwd):
    """
    Authenticate with verbose debug output (stderr) to help trace issues.
    """
    if not usr or not pwd:
        print("DEBUG: username o password vuoti", file=sys.stderr)
        return None

    usr = usr.strip()
    pwd = pwd.strip()
    conn = sqlite3.connect(DB_NAME)
    try:
        u = conn.execute("SELECT * FROM users WHERE username=?", (usr,)).fetchone()
    finally:
        conn.close()

    if not u:
        print(f"DEBUG: utente '{usr}' non trovato", file=sys.stderr)
        return None

    # Schema: id, username, password, role, city, ...
    stored_hash = u[2] if len(u) > 2 else None
    print(f"DEBUG: trovato utente id={u[0]} username={u[1]} stored_hash_present={bool(stored_hash)}", file=sys.stderr)

    if not stored_hash:
        print("DEBUG: stored_hash è vuoto/None", file=sys.stderr)
        return None

    try:
        verified = pbkdf2_sha256.verify(pwd, stored_hash)
        print(f"DEBUG: pbkdf2_sha256.verify -> {verified}", file=sys.stderr)
        if verified:
            return u
        else:
            return None
    except Exception as ex:
        print(f"DEBUG: eccezione verify: {ex}", file=sys.stderr)
        return None

def register_user(u, p, r, c_city, b, q, e, rate):
    if not u or not p:
        return False, "Username e password richiesti."
    try:
        conn = sqlite3.connect(DB_NAME)
        coords = CITY_COORDS.get(c_city, (0,0))
        if r == 'paziente':
            q, e, rate = None, 0, 0
        hashed = pbkdf2_sha256.hash(p)
        sql = """INSERT INTO users
                 (username, password, role, city, lat, lon, bio, qualification, experience, hourly_rate, email, address, age, clinical_history, detailed_experience)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        conn.execute(sql, (u, hashed, r, c_city, coords[0], coords[1], b, q, e, rate, None, None, None, None, None))
        conn.commit()
        conn.close()
        return True, "✅ Registrazione OK! Effettua il login."
    except sqlite3.IntegrityError:
        return False, "❌ Errore: Username già in uso."
    except Exception as ex:
        print("ERRORE REGISTRAZIONE:", ex, file=sys.stderr)
        return False, f"❌ Errore tecnico: {ex}"

def get_landing_pros():
    conn = sqlite3.connect(DB_NAME)
    c = conn.execute("SELECT username, city, bio, lat, lon, qualification, experience, hourly_rate FROM users WHERE role='professionista'")
    pros = c.fetchall()
    conn.close()
    return pros

def get_patient_history(uid):
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT id, intervention_type as 'Tipo', status, created_at FROM requests WHERE patient_id=? ORDER BY id DESC", conn, params=(uid,))
    conn.close()
    return df

def get_pro_open_jobs(city, my_id):
    conn = sqlite3.connect(DB_NAME)
    query = """
    SELECT r.id as ID, CASE WHEN r.target_pro_id = ? THEN '⭐ ESCLUSIVA' ELSE 'Pubblica' END as Tipo,
           u.username as Paziente, r.intervention_type, r.description, r.city
    FROM requests r JOIN users u ON r.patient_id = u.id
    WHERE r.status='Aperta' AND ((r.city=? AND r.target_pro_id IS NULL) OR r.target_pro_id=?)
    ORDER BY r.target_pro_id DESC, r.id DESC
    """
    df = pd.read_sql_query(query, conn, params=(my_id, city, my_id))
    conn.close()
    return df

def get_pro_my_jobs(pro_id):
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT r.id, u.username as Paziente, r.intervention_type, r.status FROM requests r JOIN users u ON r.patient_id = u.id WHERE r.professional_id=?", conn, params=(pro_id,))
    conn.close()
    return df

def submit_request(uid, cat, desc, city, target_id):
    conn = sqlite3.connect(DB_NAME)
    tgt = int(target_id) if (target_id and str(target_id).isdigit() and int(target_id) > 0) else None
    conn.execute("INSERT INTO requests (patient_id, professional_id, target_pro_id, intervention_type, description, city, status, created_at) VALUES (?, NULL, ?, ?, ?, ?, 'Aperta', ?)",
                 (uid, tgt, cat, desc, city, str(datetime.date.today())))
    conn.commit()
    conn.close()
    return get_patient_history(uid)

def accept_request(req_id, pro_id, city):
    if not req_id:
        return False, "⚠️ ID nullo", pd.DataFrame(), pd.DataFrame()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    check = c.execute("SELECT id FROM requests WHERE id=? AND status='Aperta' AND ((city=? AND target_pro_id IS NULL) OR target_pro_id=?)", (req_id, city, pro_id)).fetchone()
    if not check:
        conn.close()
        return False, "❌ Errore: richiesta non disponibile.", get_pro_open_jobs(city, pro_id), get_pro_my_jobs(pro_id)
    c.execute("UPDATE requests SET status='In Carico', professional_id=? WHERE id=?", (pro_id, req_id))
    conn.commit()
    conn.close()
    return True, f"✅ Presa in carico ID {req_id}", get_pro_open_jobs(city, pro_id), get_pro_my_jobs(pro_id)

def get_active_chats(user_id, role):
    conn = sqlite3.connect(DB_NAME)
    if role == 'paziente':
        q = "SELECT id, intervention_type || ' (ID: ' || id || ')' as label FROM requests WHERE patient_id=? AND status='In Carico'"
        data = pd.read_sql_query(q, conn, params=(user_id,))
    else:
        q = "SELECT id, intervention_type || ' (ID: ' || id || ')' as label FROM requests WHERE professional_id=? AND status='In Carico'"
        data = pd.read_sql_query(q, conn, params=(user_id,))
    conn.close()
    return list(zip(data['label'], data['id'])) if not data.empty else []

def get_chat_history(req_id):
    if not req_id:
        return []
    conn = sqlite3.connect(DB_NAME)
    msgs = conn.execute("SELECT sender_id, content FROM messages WHERE request_id=? ORDER BY id ASC", (req_id,)).fetchall()
    conn.close()
    return msgs

def send_chat_msg(req_id, user_id, msg):
    if not req_id or not msg:
        return get_chat_history(req_id)
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO messages (request_id, sender_id, content, timestamp) VALUES (?, ?, ?, ?)", (req_id, user_id, msg, str(datetime.datetime.now())))
    conn.commit()
    conn.close()
    return get_chat_history(req_id)

def get_ai_rec(text, city):
    if not AI_AVAILABLE:
        return "AI non disponibile", "", pd.DataFrame(), None
    emb = model.encode(text, convert_to_tensor=True)
    kb = model.encode(KNOWLEDGE_BASE, convert_to_tensor=True)
    best = KNOWLEDGE_BASE[torch.argmax(util.cos_sim(emb, kb)[0]).item()]
    quals = INTERVENTION_MAPPING[best]
    conn = sqlite3.connect(DB_NAME)
    ph = ','.join('?'*len(quals))
    df = pd.read_sql_query(f"SELECT id as ID, username, qualification, hourly_rate FROM users WHERE role='professionista' AND city=? AND qualification IN ({ph})", conn, params=[city]+quals)
    conn.close()
    return f"✅ Bisogno: {best}", "OK", df, best

def create_map_html(pros):
    m = folium.Map([42, 12.5], zoom_start=6)
    mc = MarkerCluster().add_to(m)
    for p in pros:
        try:
            folium.Marker([p[3], p[4]], popup=f"{html.escape(p[0])} ({html.escape(str(p[5]) if p[5] else '')})").add_to(mc)
        except Exception:
            pass
    return m._repr_html_()

def update_full_profile(uid, role, pwd, bio, email, address, age, clinical, det_exp, qual=None, num_exp=None, rate=None):
    conn = sqlite3.connect(DB_NAME)
    try:
        # Hash password if provided (non-empty)
        pwd_hashed = pbkdf2_sha256.hash(pwd) if pwd else None

        if role == 'paziente':
            if pwd_hashed:
                conn.execute("UPDATE users SET password=?, bio=?, email=?, address=?, age=?, clinical_history=? WHERE id=?", (pwd_hashed, bio, email, address, age, clinical, uid))
            else:
                conn.execute("UPDATE users SET bio=?, email=?, address=?, age=?, clinical_history=? WHERE id=?", (bio, email, address, age, clinical, uid))
        else:
            if pwd_hashed:
                conn.execute("UPDATE users SET password=?, bio=?, email=?, address=?, age=?, detailed_experience=?, qualification=?, experience=?, hourly_rate=? WHERE id=?", (pwd_hashed, bio, email, address, age, det_exp, qual, num_exp, rate, uid))
            else:
                conn.execute("UPDATE users SET bio=?, email=?, address=?, age=?, detailed_experience=?, qualification=?, experience=?, hourly_rate=? WHERE id=?", (bio, email, address, age, det_exp, qual, num_exp, rate, uid))
        conn.commit()
        return True, "✅ Profilo salvato!"
    except Exception as e:
        print(f"ERRORE update_full_profile: {e}", file=sys.stderr)
        return False, f"❌ Errore: {e}"
    finally:
        conn.close()

# --- STREAMLIT UI ---
st.set_page_config(page_title="CareConnect - Streamlit", layout="wide")
st.title("🏥 CareConnect (Streamlit)")

if 'user' not in st.session_state:
    st.session_state['user'] = None  # will hold DB row tuple

# Sidebar: Login / Register / Logout + Debug
with st.sidebar:
    st.header("Accesso")
    if st.session_state['user'] is None:
        login_user = st.text_input("Username")
        login_pass = st.text_input("Password", type="password")
        if st.button("Login"):
            user = authenticate(login_user, login_pass)
            if user:
                st.session_state['user'] = user
                st.success(f"Benvenuto {user[1]}!")
                st.experimental_rerun()
            else:
                st.error("Credenziali non valide. Controlla la console per i log di debug.")
        st.markdown("---")
        st.subheader("Registrazione")
        reg_u = st.text_input("Nuovo username", key="reg_u")
        reg_p = st.text_input("Nuova password", type="password", key="reg_p")
        reg_role = st.radio("Ruolo", options=["paziente", "professionista"], index=0, key="reg_role")
        reg_city = st.selectbox("Città", options=list(CITY_COORDS.keys()), key="reg_city")
        reg_bio = st.text_area("Bio", key="reg_bio")
        if reg_role == "professionista":
            reg_q = st.selectbox("Qualifica", options=ALL_QUALIFICATIONS)
            reg_e = st.number_input("Anni esperienza", min_value=0, value=0)
            reg_r = st.number_input("Tariffa oraria (€)", min_value=0.0, value=10.0)
        else:
            reg_q, reg_e, reg_r = None, 0, 0
        if st.button("Registrati"):
            ok, msg = register_user(reg_u, reg_p, reg_role, reg_city, reg_bio, reg_q, reg_e, reg_r)
            if ok:
                st.success(msg)
            else:
                st.error(msg)
    else:
        user = st.session_state['user']
        st.write(f"Connesso come: {user[1]} ({user[3]})")
        if st.button("Logout"):
            st.session_state['user'] = None
            st.experimental_rerun()

    # Debug tools (developer only)
    st.markdown("---")
    st.subheader("Debug (dev only)")
    dbg_user = st.text_input("Debug username", key="dbg_user")
    if st.button("Mostra hash", key="dbg_btn"):
        st.text_area("Hash utente", value=debug_show_hash(dbg_user), height=140)

st.markdown("## 🏠 Home")
# Landing: map + cards
pros = get_landing_pros()
col1, col2 = st.columns([2,1])
with col1:
    st.markdown("### Mappa professionisti")
    html_map = create_map_html(pros)
    st.components.v1.html(html_map, height=500)
with col2:
    st.markdown("### Professionisti (schede)")
    for p in pros:
        st.markdown(f"**{p[0]}** — {p[5] or ''} — €{p[7]}/h")
    if not pros:
        st.info("Nessun professionista presente.")

st.markdown("---")

# Se utente loggato -> Dashboard
if st.session_state['user'] is not None:
    usr = st.session_state['user']
    uid = usr[0]
    uname = usr[1]
    role = usr[3]
    city = usr[4]
    st.success(f"Benvenuto {uname} — {role} — {city}")

    if role == 'paziente':
        st.header("👤 Area Paziente")
        tab1, tab2, tab3, tab4 = st.tabs(["Richiedi", "Chat", "Storico", "Profilo"])

        with tab1:
            st.subheader("Analisi AI (opzionale)")
            ai_text = st.text_input("Descrivi il bisogno per farti aiutare dall'AI")
            if st.button("Analizza con AI"):
                if not AI_AVAILABLE:
                    st.error("AI non disponibile. Installa sentence-transformers per abilitare.")
                else:
                    ai_msg, _, ai_df, best = get_ai_rec(ai_text, city)
                    st.info(ai_msg)
                    if not ai_df.empty:
                        st.dataframe(ai_df)
            st.markdown("---")
            st.subheader("Invia Richiesta")
            with st.form("send_request"):
                req_cat = st.selectbox("Categoria", options=list(INTERVENTION_MAPPING.keys()))
                req_desc = st.text_area("Dettagli")
                req_target = st.text_input("ID Professionista target (opzionale)")
                submitted = st.form_submit_button("Invia Richiesta")
                if submitted:
                    df = submit_request(uid, req_cat, req_desc, city, req_target)
                    st.success("Richiesta inviata.")
                    st.dataframe(df)

        with tab2:
            st.subheader("Chat attive")
            chats = get_active_chats(uid, 'paziente')
            if not chats:
                st.info("Nessuna chat attiva.")
            else:
                mapping = {label: rid for label, rid in chats}
                sel = st.selectbox("Seleziona chat", options=list(mapping.keys()))
                sel_id = mapping.get(sel)
                msgs = get_chat_history(sel_id)
                for m in msgs:
                    sender_id, content = m
                    if sender_id == uid:
                        st.chat_message("user").write(content)
                    else:
                        st.chat_message("assistant").write(content)
                new_msg = st.text_input("Messaggio")
                if st.button("Invia messaggio", key="pat_send"):
                    send_chat_msg(sel_id, uid, new_msg)
                    st.experimental_rerun()

        with tab3:
            st.subheader("Storico Richieste")
            hist = get_patient_history(uid)
            st.dataframe(hist)

        with tab4:
            st.subheader("Profilo")
            # Unpack profile fields
            email, addr, age, clinic, bio = usr[11], usr[12], usr[13], usr[14], usr[7]
            with st.form("profile_pat"):
                p_email = st.text_input("Email", value=email or "")
                p_addr = st.text_input("Indirizzo", value=addr or "")
                p_age = st.number_input("Età", value=int(age) if age else 0)
                p_clinic = st.text_area("Storia Clinica", value=clinic or "")
                p_bio = st.text_area("Bio", value=bio or "")
                p_pass = st.text_input("Password (lascia vuoto per non cambiare)", type="password", value="")
                if st.form_submit_button("Salva Profilo"):
                    ok, msg = update_full_profile(uid, 'paziente', p_pass, p_bio, p_email, p_addr, p_age, p_clinic, None)
                    if ok:
                        st.success(msg)
                        # reload user info
                        if p_pass:
                            st.session_state['user'] = authenticate(uname, p_pass)
                        else:
                            st.session_state['user'] = conn_fetch_user_by_username(uname)
                        st.experimental_rerun()
                    else:
                        st.error(msg)

    else:
        st.header("💼 Area Professionista")
        tab1, tab2, tab3 = st.tabs(["Lavoro", "Chat", "Profilo"])
        with tab1:
            st.subheader("Richieste disponibili")
            if st.button("Aggiorna"):
                st.experimental_rerun()
            open_jobs = get_pro_open_jobs(city, uid)
            st.dataframe(open_jobs)
            st.markdown("Accetta richieste inserendo l'ID")
            accept_id = st.number_input("ID richiesta da accettare", min_value=0, value=0)
            if st.button("Accetta"):
                ok, msg, new_open, new_my = accept_request(accept_id, uid, city)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                st.dataframe(new_open)
                st.dataframe(new_my)
            st.markdown("---")
            st.subheader("Miei Pazienti / Carichi")
            my_jobs = get_pro_my_jobs(uid)
            st.dataframe(my_jobs)

        with tab2:
            st.subheader("Chat attive")
            chats = get_active_chats(uid, 'professionista')
            if not chats:
                st.info("Nessuna chat attiva.")
            else:
                mapping = {label: rid for label, rid in chats}
                sel = st.selectbox("Seleziona chat", options=list(mapping.keys()))
                sel_id = mapping.get(sel)
                msgs = get_chat_history(sel_id)
                for m in msgs:
                    sender_id, content = m
                    if sender_id == uid:
                        st.chat_message("user").write(content)
                    else:
                        st.chat_message("assistant").write(content)
                new_msg = st.text_input("Messaggio")
                if st.button("Invia messaggio pro", key="pro_send"):
                    send_chat_msg(sel_id, uid, new_msg)
                    st.experimental_rerun()

        with tab3:
            st.subheader("Profilo Professionista")
            # Unpack profile
            email, addr, age, det_exp, bio, qual, exp, rate = usr[11], usr[12], usr[13], usr[15], usr[7], usr[8], usr[9], usr[10]
            with st.form("profile_pro"):
                p_email = st.text_input("Email", value=email or "")
                p_addr = st.text_input("Studio/Indirizzo", value=addr or "")
                p_age = st.number_input("Età", value=int(age) if age else 0)
                p_cv = st.text_area("CV / Dettagli", value=det_exp or "")
                p_bio = st.text_area("Bio", value=bio or "")
                p_pass = st.text_input("Password (lascia vuoto per non cambiare)", type="password", value="")
                p_q = st.selectbox("Qualifica", options=ALL_QUALIFICATIONS, index=ALL_QUALIFICATIONS.index(qual) if qual in ALL_QUALIFICATIONS else 0)
                p_e = st.number_input("Anni esperienza", min_value=0, value=int(exp) if exp else 0)
                p_r = st.number_input("Tariffa oraria (€)", min_value=0.0, value=float(rate) if rate else 10.0)
                if st.form_submit_button("Salva Profilo"):
                    ok, msg = update_full_profile(uid, 'professionista', p_pass, p_bio, p_email, p_addr, p_age, None, p_cv, p_q, p_e, p_r)
                    if ok:
                        st.success(msg)
                        if p_pass:
                            st.session_state['user'] = authenticate(uname, p_pass)
                        else:
                            st.session_state['user'] = conn_fetch_user_by_username(uname)
                        st.experimental_rerun()
                    else:
                        st.error(msg)

else:
    st.info("Effettua il login o registrati dalla sidebar per accedere alla dashboard.")
