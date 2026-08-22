"""
Class Attendance — Streamlit + Supabase
Open on your phone, mark attendance, done. No laptop needed.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from supabase import create_client

st.set_page_config(page_title="Attendance", page_icon="✅", layout="centered")


# ----------------------------------------------------------------------
# Connection
# ----------------------------------------------------------------------
@st.cache_resource
def get_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


sb = get_client()


# ----------------------------------------------------------------------
# PIN gate  (the app URL is public, so this keeps students out)
# ----------------------------------------------------------------------
if "authed" not in st.session_state:
    st.session_state.authed = False

if not st.session_state.authed:
    st.title("Class Attendance")
    st.caption("Enter your PIN to continue.")
    pin = st.text_input("Teacher PIN", type="password", label_visibility="collapsed",
                        placeholder="Teacher PIN")
    if st.button("Unlock", type="primary", use_container_width=True):
        if pin == st.secrets["TEACHER_PIN"]:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("That PIN doesn't match. Try again.")
    st.stop()


# ----------------------------------------------------------------------
# Data helpers
# ----------------------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_students():
    rows = sb.table("students").select("*").order("roll_no").execute().data
    return rows or []


def fetch_attendance(start, end, section=None):
    q = (sb.table("attendance")
           .select("roll_no, date, present, students(name, section)")
           .gte("date", str(start))
           .lte("date", str(end)))
    rows = q.execute().data or []
    flat = []
    for r in rows:
        stu = r.get("students") or {}
        if section and section != "All" and stu.get("section") != section:
            continue
        flat.append({
            "roll_no": r["roll_no"],
            "name": stu.get("name", ""),
            "section": stu.get("section", ""),
            "date": r["date"],
            "present": r["present"],
        })
    return flat


def existing_marks(day, rolls):
    """Marks already saved for this date, so re-opening a day shows what you saved."""
    if not rolls:
        return {}
    rows = (sb.table("attendance")
              .select("roll_no, present")
              .eq("date", str(day))
              .in_("roll_no", rolls)
              .execute().data) or []
    return {r["roll_no"]: r["present"] for r in rows}


# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------
students = fetch_students()

if not students:
    st.title("Class Attendance")
    st.info("No students yet. Add them in the **Students** tab below.")

sections = sorted({s.get("section") or "—" for s in students})
section_options = ["All"] + sections if len(sections) > 1 else (sections or ["All"])

tab_mark, tab_students, tab_reports = st.tabs(["Mark", "Students", "Reports"])


# ----------------------------------------------------------------------
# TAB 1 — Mark attendance
# ----------------------------------------------------------------------
with tab_mark:
    c1, c2 = st.columns(2)
    day = c1.date_input("Date", date.today())
    section = c2.selectbox("Section", section_options)

    roster = [s for s in students
              if section in ("All",) or (s.get("section") or "—") == section]

    if not roster:
        st.info("No students in this section.")
    else:
        rolls = [s["roll_no"] for s in roster]

        # Load saved marks when the date or section changes
        stamp = f"{day}|{section}"
        if st.session_state.get("loaded_stamp") != stamp:
            saved = existing_marks(day, rolls)
            for r in rolls:
                st.session_state[f"chk_{r}"] = saved.get(r, True)  # default: present
            st.session_state.loaded_stamp = stamp
            st.session_state.was_saved = bool(saved)

        if st.session_state.get("was_saved"):
            st.caption("Attendance for this date is already saved. Edits will overwrite it.")

        b1, b2 = st.columns(2)
        if b1.button("All present", use_container_width=True):
            for r in rolls:
                st.session_state[f"chk_{r}"] = True
            st.rerun()
        if b2.button("All absent", use_container_width=True):
            for r in rolls:
                st.session_state[f"chk_{r}"] = False
            st.rerun()

        st.divider()

        for s in roster:
            st.checkbox(
                f'{s["roll_no"]} · {s["name"]}',
                key=f'chk_{s["roll_no"]}',
            )

        st.divider()

        present_count = sum(1 for r in rolls if st.session_state.get(f"chk_{r}"))
        st.metric("Present", f"{present_count} / {len(rolls)}")

        if st.button("Save attendance", type="primary", use_container_width=True):
            payload = [
                {"roll_no": r, "date": str(day), "present": bool(st.session_state[f"chk_{r}"])}
                for r in rolls
            ]
            try:
                sb.table("attendance").upsert(payload, on_conflict="roll_no,date").execute()
                st.session_state.was_saved = True
                st.success(f"Saved — {present_count} present, {len(rolls) - present_count} absent.")
            except Exception as e:
                st.error(f"Could not save: {e}")


# ----------------------------------------------------------------------
# TAB 2 — Students
# ----------------------------------------------------------------------
with tab_students:
    st.subheader("Roster")

    if students:
        st.dataframe(pd.DataFrame(students)[["roll_no", "name", "section"]],
                     use_container_width=True, hide_index=True)

    with st.expander("Add one student"):
        r = st.text_input("Roll number")
        n = st.text_input("Name")
        sec = st.text_input("Section", value="A")
        if st.button("Add student"):
            if r.strip() and n.strip():
                try:
                    sb.table("students").upsert(
                        {"roll_no": r.strip(), "name": n.strip(), "section": sec.strip()}
                    ).execute()
                    fetch_students.clear()
                    st.success(f"Added {n.strip()}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add: {e}")
            else:
                st.warning("Roll number and name are both required.")

    with st.expander("Upload a CSV"):
        st.caption("Columns: roll_no, name, section")
        up = st.file_uploader("Choose file", type="csv")
        if up is not None:
            df = pd.read_csv(up, dtype=str).fillna("")
            missing = {"roll_no", "name"} - set(df.columns)
            if missing:
                st.error(f"Missing column(s): {', '.join(missing)}")
            else:
                st.dataframe(df.head(), use_container_width=True, hide_index=True)
                if st.button(f"Import {len(df)} students"):
                    if "section" not in df.columns:
                        df["section"] = "A"
                    recs = df[["roll_no", "name", "section"]].to_dict("records")
                    try:
                        sb.table("students").upsert(recs, on_conflict="roll_no").execute()
                        fetch_students.clear()
                        st.success(f"Imported {len(recs)} students.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Import failed: {e}")

    with st.expander("Remove a student"):
        if students:
            label = {f'{s["roll_no"]} · {s["name"]}': s["roll_no"] for s in students}
            pick = st.selectbox("Student", list(label.keys()))
            st.caption("This also deletes their attendance history.")
            if st.button("Remove", type="secondary"):
                try:
                    sb.table("attendance").delete().eq("roll_no", label[pick]).execute()
                    sb.table("students").delete().eq("roll_no", label[pick]).execute()
                    fetch_students.clear()
                    st.success("Removed.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not remove: {e}")


# ----------------------------------------------------------------------
# TAB 3 — Reports
# ----------------------------------------------------------------------
with tab_reports:
    st.subheader("Reports")

    c1, c2 = st.columns(2)
    start = c1.date_input("From", date.today() - timedelta(days=30), key="rep_from")
    end = c2.date_input("To", date.today(), key="rep_to")
    rep_section = st.selectbox("Section", section_options, key="rep_sec")

    if st.button("Run report", use_container_width=True):
        rows = fetch_attendance(start, end, rep_section)
        if not rows:
            st.info("No attendance recorded in this range.")
        else:
            df = pd.DataFrame(rows)
            st.session_state.report_df = df

    df = st.session_state.get("report_df")
    if df is not None and not df.empty:
        summary = (df.groupby(["roll_no", "name"])
                     .agg(days=("present", "size"), present=("present", "sum"))
                     .reset_index())
        summary["percent"] = (summary["present"] / summary["days"] * 100).round(1)
        summary = summary.sort_values("percent")

        st.dataframe(summary, use_container_width=True, hide_index=True)

        low = summary[summary["percent"] < 75]
        if not low.empty:
            st.warning(f"{len(low)} student(s) below 75%.")

        grid = df.pivot_table(index=["roll_no", "name"], columns="date",
                              values="present", aggfunc="first")
        grid = grid.replace({True: "P", False: "A"}).fillna("")

        st.download_button("Download summary (CSV)",
                           summary.to_csv(index=False).encode(),
                           file_name=f"summary_{start}_{end}.csv",
                           mime="text/csv", use_container_width=True)
        st.download_button("Download day-by-day (CSV)",
                           grid.to_csv().encode(),
                           file_name=f"daywise_{start}_{end}.csv",
                           mime="text/csv", use_container_width=True)

st.divider()
if st.button("Lock app"):
    st.session_state.authed = False
    st.rerun()
