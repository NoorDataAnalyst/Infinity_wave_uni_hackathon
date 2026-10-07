"""
NovaWorks Project Manager CRM - The Infinity Hack '26
Run:
    pip install streamlit requests
    export GEMINI_API_KEY=...        # or OPENAI_API_KEY=...
    streamlit run app.py
Demo login: any seeded email, password Demo123!
"""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3

import requests
import streamlit as st

DB = "novaworks.db"
PASSWORD = "Demo123!"

USERS = [
    ("ADM01", "Admin", "admin@novaworks.example", "ADMIN", "Administrator", "All access"),
    ("PM01", "Ayesha Khan", "ayesha@novaworks.example", "MANAGER", "Web PM", ""),
    ("PM02", "Bilal Ahmed", "bilal@novaworks.example", "MANAGER", "Mobile PM", ""),
    ("PM03", "Hina Malik", "hina@novaworks.example", "MANAGER", "AI PM", ""),
    ("DEV01", "Ali Raza", "ali@novaworks.example", "AGENT", "", "React, frontend integration"),
    ("DEV02", "Hamza Shah", "hamza@novaworks.example", "AGENT", "", "Node.js, databases, APIs"),
    ("DEV03", "Sara Noor", "sara@novaworks.example", "AGENT", "", "Flutter, mobile UI"),
    ("DEV04", "Usman Tariq", "usman@novaworks.example", "AGENT", "", "Flutter, integration, testing"),
    ("DEV05", "Zain Abbas", "zain@novaworks.example", "AGENT", "", "LLMs, extraction, prompts"),
    ("DEV06", "Maryam Asif", "maryam@novaworks.example", "AGENT", "", "Retrieval, document processing"),
]


# ---------------------------------------------------------------- database
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, role TEXT,
            specialization TEXT, skills TEXT, password_hash TEXT);
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, clientName TEXT,
            description TEXT, managerId TEXT REFERENCES users(id), deadline TEXT);
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            projectId INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT, description TEXT, assigneeId TEXT REFERENCES users(id),
            deadline TEXT, estimatedHours REAL);
        """
    )
    if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
            [u + (hash_pw(PASSWORD),) for u in USERS],
        )
    con.commit()
    con.close()


# ------------------------------------------------------------ RBAC queries
def get_projects(user):
    con = db()
    if user["role"] == "ADMIN":
        rows = con.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
    elif user["role"] == "MANAGER":
        rows = con.execute(
            "SELECT * FROM projects WHERE managerId=? ORDER BY id DESC", (user["id"],)
        ).fetchall()
    else:  # AGENT: only projects containing their tasks
        rows = con.execute(
            "SELECT * FROM projects WHERE id IN "
            "(SELECT projectId FROM tasks WHERE assigneeId=?) ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
    con.close()
    return rows


def get_tasks(user, project_id=None):
    q = (
        "SELECT t.*, p.name AS projectName, u.name AS assigneeName FROM tasks t "
        "JOIN projects p ON p.id=t.projectId LEFT JOIN users u ON u.id=t.assigneeId WHERE 1=1"
    )
    args = []
    if user["role"] == "MANAGER":
        q += " AND p.managerId=?"
        args.append(user["id"])
    elif user["role"] == "AGENT":
        q += " AND t.assigneeId=?"
        args.append(user["id"])
    if project_id is not None:
        q += " AND t.projectId=?"
        args.append(project_id)
    con = db()
    rows = con.execute(q + " ORDER BY t.deadline", args).fetchall()
    con.close()
    return rows


def team_directory():
    con = db()
    rows = con.execute(
        "SELECT id,name,email,role,specialization,skills FROM users ORDER BY id"
    ).fetchall()
    con.close()
    return rows


# --------------------------------------------------------------- AI parser
def build_prompt(transcript):
    team = [
        {"id": u["id"], "name": u["name"], "role": u["role"],
         "specialization": u["specialization"], "skills": u["skills"]}
        for u in team_directory() if u["role"] != "ADMIN"
    ]
    return f"""You are a project-management assistant for NovaWorks Technologies.
Today's date is {dt.date.today().isoformat()}.

TEAM DIRECTORY (use ONLY these ids):
{json.dumps(team, indent=2)}

Read the meeting transcript and extract every project and its tasks.

Rules:
- managerId must be a MANAGER id (PM01, PM02, PM03) whose specialization fits the project.
- assigneeId must be an AGENT id (DEV01..DEV06) whose skills fit the task.
- Transcripts often change their mind. ALWAYS use the FINAL decision: the last revised
  deadline, the last revised estimated hours, and the last assignee correction.
  Ignore earlier superseded values. Do not create tasks that were cancelled.
- Resolve relative dates ("next Friday") against today's date. Dates are YYYY-MM-DD.
- estimatedHours is a number.
- Respond with ONLY valid JSON, no markdown, matching exactly:
{{"projects":[{{"name":"","clientName":"","description":"","managerId":"","deadline":"YYYY-MM-DD",
"tasks":[{{"title":"","description":"","assigneeId":"","deadline":"YYYY-MM-DD","estimatedHours":10}}]}}]}}

TRANSCRIPT:
\"\"\"
{transcript}
\"\"\""""


def call_llm(prompt, provider, key):
    if provider == "Gemini":
        # Use a stable production model name
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        
        # Pass API key directly in query parameters
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        
        r = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0
                },
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def save_projects(data):
    """Validate against the directory and persist. Returns list of saved project ids."""
    users = {u["id"]: u for u in team_directory()}
    managers = [i for i, u in users.items() if u["role"] == "MANAGER"]
    agents = [i for i, u in users.items() if u["role"] == "AGENT"]
    con = db()
    saved = []
    try:
        for p in data.get("projects", []):
            mid = p.get("managerId")
            if mid not in managers:
                raise ValueError(f"Invalid managerId '{mid}' for project '{p.get('name')}'")
            cur = con.execute(
                "INSERT INTO projects(name,clientName,description,managerId,deadline) VALUES(?,?,?,?,?)",
                (p.get("name"), p.get("clientName"), p.get("description"), mid, p.get("deadline")),
            )
            pid = cur.lastrowid
            for t in p.get("tasks", []):
                aid = t.get("assigneeId")
                if aid not in agents:
                    raise ValueError(f"Invalid assigneeId '{aid}' for task '{t.get('title')}'")
                con.execute(
                    "INSERT INTO tasks(projectId,title,description,assigneeId,deadline,estimatedHours) "
                    "VALUES(?,?,?,?,?,?)",
                    (pid, t.get("title"), t.get("description"), aid, t.get("deadline"),
                     float(t.get("estimatedHours") or 0)),
                )
            saved.append(pid)
        con.commit()  # all-or-nothing
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return saved


# --------------------------------------------------------------------- UI
def task_card(t, show_assignee=True):
    with st.container(border=True):
        st.markdown(f"**{t['title']}**  \n:grey[Project: {t['projectName']}]")
        st.write(t["description"])
        c = st.columns(3)
        c[0].caption(f"📅 Deadline: {t['deadline']}")
        c[1].caption(f"⏱ {t['estimatedHours']:g} hrs")
        if show_assignee:
            c[2].caption(f"👤 {t['assigneeName']}")


def project_block(user, p):
    with st.expander(f"📁 {p['name']} — {p['clientName']} (due {p['deadline']})", expanded=True):
        st.write(p["description"])
        for t in get_tasks(user, p["id"]):
            task_card(t)


def login_screen():
    st.title("NovaWorks Project Manager")
    st.subheader("Sign in")
    users = team_directory()
    label = {f"{u['name']} ({u['role']}) — {u['email']}": u for u in users}
    choice = st.selectbox("Account", list(label))
    pw = st.text_input("Password", type="password", placeholder="Demo123!")
    if st.button("Login", type="primary"):
        con = db()
        row = con.execute(
            "SELECT * FROM users WHERE id=? AND password_hash=?",
            (label[choice]["id"], hash_pw(pw)),
        ).fetchone()
        con.close()
        if row:
            st.session_state.user = dict(row)
            st.rerun()
        else:
            st.error("Wrong password.")


def admin_dashboard(user):
    st.header("Admin Dashboard")
    with st.sidebar.expander("AI settings", expanded=False):
        provider = st.selectbox("Provider", ["Gemini", "OpenAI"])
        env = "GEMINI_API_KEY" if provider == "Gemini" else "OPENAI_API_KEY"
        key = st.text_input(f"{env}", type="password", value=os.getenv(env, ""))

    transcript = st.text_area("Meeting transcript", height=260,
                              placeholder="Paste the meeting transcript here...")
    if st.button("Create from Transcript", type="primary"):
        if not transcript.strip():
            st.warning("Paste a transcript first.")
        elif not key:
            st.warning("Add an API key in the sidebar (AI settings) or via environment variable.")
        else:
            try:
                with st.spinner("Reading transcript and creating projects..."):
                    data = parse_json(call_llm(build_prompt(transcript), provider, key))
                    ids = save_projects(data)
                st.session_state.last_created = ids
                st.success(f"Created {len(ids)} project(s).")
            except Exception as e:
                st.error(f"Could not process transcript: {e}")

    created = st.session_state.get("last_created", [])
    if created:
        st.subheader("Just created")
        for p in [p for p in get_projects(user) if p["id"] in created]:
            project_block(user, p)

    st.subheader("All projects")
    for p in get_projects(user):
        if p["id"] not in created:
            project_block(user, p)


def manager_view(user):
    st.header("My Projects")
    projects = get_projects(user)
    if not projects:
        st.info("No projects assigned to you yet.")
    for p in projects:
        project_block(user, p)


def agent_view(user):
    st.header("My Tasks")
    tasks = get_tasks(user)
    if not tasks:
        st.info("No tasks assigned to you yet.")
    for t in tasks:
        task_card(t, show_assignee=False)


def directory_view():
    st.header("Team Directory")
    st.dataframe(
        [{"Name": u["name"], "Email": u["email"], "Role": u["role"],
          "Specialization": u["specialization"], "Skills": u["skills"]}
         for u in team_directory()],
        use_container_width=True, hide_index=True,
    )


def main():
    st.set_page_config(page_title="NovaWorks PM", page_icon="📋", layout="wide")
    init_db()
    user = st.session_state.get("user")
    if not user:
        return login_screen()

    st.sidebar.markdown(f"**{user['name']}**  \n{user['role']}")
    pages = {"ADMIN": ["Dashboard", "Team Directory"],
             "MANAGER": ["My Projects", "Team Directory"],
             "AGENT": ["My Tasks", "Team Directory"]}[user["role"]]
    page = st.sidebar.radio("Navigate", pages)
    if st.sidebar.button("Log out"):
        st.session_state.clear()
        st.rerun()

    if page == "Team Directory":
        directory_view()
    elif user["role"] == "ADMIN":
        admin_dashboard(user)
    elif user["role"] == "MANAGER":
        manager_view(user)
    else:
        agent_view(user)


main()
