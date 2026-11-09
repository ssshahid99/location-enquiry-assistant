"""Demo UI. Run from the project root:
    $env:PYTHONPATH=".;src"; streamlit run app/streamlit_app.py
Tabs: Ask (direct grounded answers) | Cases (router + Planner/Executor/Critic agent) | Approvals (one authorised human decides).
Nothing is ever sent externally: approving applies an internal update only (case status, owner, next action, tracker)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import streamlit as st  # noqa: E402

from lbs_rag.agent import orchestrator  # noqa: E402
from lbs_rag.generate import meta  # noqa: E402
from lbs_rag.generate.answer import answer  # noqa: E402
from lbs_rag.safety.pii import redact_with_report  # noqa: E402
from lbs_rag.store import cases, handlers  # noqa: E402
from lbs_rag.store import repositories as repo  # noqa: E402

st.set_page_config(page_title="Location Services Copilot", layout="wide")
st.title("Location Services Sales & Support Copilot")

st.sidebar.header("What can I ask?")
st.sidebar.caption("Click an example to fill the question box, or type 'what can you do?'.")
for _ex in meta.EXAMPLES:
    if st.sidebar.button(_ex, key="ex_" + _ex[:30]):
        st.session_state["q"] = _ex
        st.session_state["case_msg"] = _ex

ask_tab, cases_tab, approve_tab = st.tabs(["Ask", "Cases", "Approvals"])

ROUTE_LABEL = {"rag": "Answered directly from documentation", "agent": "Handled as a case by the agent (Planner, Executor, Critic)",
               "human": "Needs a person: case and handoff prepared", "refused": "Not processed (scope or safety rule)"}


def show_sources(citations):
    if citations:
        st.subheader("Sources")
        for c in citations:
            st.markdown(f"**[{c['n']}]** {c['provider']} - [{c['doc_url']}]({c['doc_url']})  \n"
                        f"<small>fetched {c.get('fetched_at', '')}</small>", unsafe_allow_html=True)


# ------------------------------------------------------------------------------------------------ Ask
with ask_tab:
    q = st.text_area("Customer question", height=110, key="q",
                     placeholder="e.g. Ola Maps Reverse Geocoding for 20,000,000 lookups a month. Cost?  Or: what can you do?")
    rewrite = st.checkbox("Query rewriting", value=True, help="Improves passage retrieval and citation accuracy; one extra model call.")
    if st.button("Answer", type="primary") and q.strip():
        clean, found = redact_with_report(q)
        if found:
            st.warning(f"Removed sensitive data before processing: {', '.join(sorted(set(found)))}")
        with st.spinner("Retrieving sources and drafting..."):
            a = answer(clean, rewrite=rewrite)
        if a.get("refused"):
            st.warning("Not processed: this question is outside what this system handles (scope or safety rule).")
        elif a["escalate"]:
            st.error(f"Escalation proposed ({a['reason']}). A human must approve it in the Approvals tab; nothing was sent.")
        elif a["abstained"]:
            st.info(f"The system declined to state some or all facts ({a['reason'] or 'insufficient verified sources'}).")
        st.markdown(a["text"])
        show_sources(a["citations"])
        with st.expander("Trace"):
            st.json({k: a[k] for k in ("trace_id", "abstained", "escalate", "reason", "tool_calls")}, expanded=False)

# ------------------------------------------------------------------------------------------------ Cases
with cases_tab:
    st.caption("The router decides: simple questions are answered directly; incidents, missing information and sales qualification become a "
               "case (Planner -> Executor -> Critic); contract and security matters go to a person. Everything consequential waits for approval.")
    left, right = st.columns([3, 2])
    with left:
        msg = st.text_area("Customer message", height=110, key="case_msg",
                           placeholder="e.g. PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    with right:
        customer = st.text_input("Customer", value="Demo customer")
        go = st.button("Handle message", type="primary")
    if go and msg.strip():
        with st.spinner("Routing and working on it..."):
            con = repo.connect()
            try:
                st.session_state["last_result"] = orchestrator.handle_message(con, msg, customer=customer)
            finally:
                con.close()

    r = st.session_state.get("last_result")
    if r:
        st.subheader(ROUTE_LABEL.get(r["route"], r["route"]))
        st.caption(f"Why: {r['reason']}")
        if r.get("redacted"):
            st.warning(f"Removed sensitive data before processing: {', '.join(r['redacted'])}")
        if r["route"] in ("rag", "refused"):
            st.markdown(r.get("text", ""))
            show_sources(r.get("citations", []))
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Workflow", r.get("workflow") or "-")
            c2.metric("Category", r.get("category") or "-")
            c3.metric("Urgency", r.get("urgency") or "-")
            c4.metric("Case status", r.get("status") or "-")
            st.info(f"Case `{r['case_id']}`. {r.get('next_step', '')}")
            if r.get("questions"):
                st.markdown("**Questions to ask the customer**")
                for qq in r["questions"]:
                    st.write(f"- {'(required) ' if qq['required'] else ''}{qq['question']}")
            if r.get("needs_human_followup"):
                st.warning("A person should follow up on: " + ", ".join(r["needs_human_followup"]))
            if r.get("escalation", {}).get("recommend"):
                e = r["escalation"]
                st.error(f"Escalation recommended ({e['category']}, {e['mode']}): to {e['owner']}. Missing evidence: "
                         f"{', '.join(e['missing_evidence']) or 'none'}. Policy status: {e['policy_status']}.")
            bad = [v for v in r.get("validations", []) if v.get("ok") is False]
            if bad:
                st.markdown("**Problems found in the customer's request**")
                for v in bad:
                    st.write(f"- {v['detail']}")
            crit = r.get("critic")
            if crit:
                (st.success if crit["verdict"] == "pass" else st.error)(
                    f"Critic: {crit['verdict']} after {crit['revisions']} automatic revision(s)"
                    + ("" if crit["handoff_ready"] else "; handoff not complete: " + "; ".join(i["detail"] for i in crit["handoff_issues"])))
            if r.get("draft"):
                with st.expander("Draft reply (pending approval, never sent)", expanded=True):
                    st.text(r["draft"])
            if r.get("handoff"):
                with st.expander("Handoff package (pending approval)", expanded=True):
                    st.text(r["handoff"]["text"])
            if r.get("approvals"):
                st.markdown("**Waiting for your approval** (Approvals tab): " + ", ".join(f"#{a['id']} {a['type']}" for a in r["approvals"]))
            st.caption(f"{r['elapsed_ms'] / 1000:.1f}s")

    st.divider()
    st.subheader("Open cases")
    show_closed = st.checkbox("Show closed cases", value=False)
    con = repo.connect()
    try:
        rows = [c for c in cases.list_cases(con, limit=100) if show_closed or c["status"] != "closed"]
        if not rows:
            st.write("No open cases." if not show_closed else "No cases yet.")
        else:
            labels = {f"{c['id']} | {c['status']} | {c['workflow']}/{c['category']} | {c['customer']}": c["id"] for c in rows}
            pick = labels[st.selectbox("Case", list(labels))]
            c = cases.get_case(con, pick)
            st.write(f"Status **{c['status']}** | owner {c['owner'] or 'unassigned'} | next action: {c['next_action'] or 'not set'}")
            t1, t2, t3, t4, t5 = st.tabs(["Conversation", "Known facts and tracker", "Timeline", "Reply / handoff", "Close / reopen"])
            with t1:
                for m in cases.get_case_history(con, pick, limit=100):
                    st.markdown(f"**{m['role']}** ({m['ts']}): {m['content']}")
            with t2:
                st.write("Known facts (working memory):", cases.known_facts(con, pick))
                st.write("Approved tracker:", cases.get_tracker(con, pick) or "nothing approved yet")
            with t3:
                for ev in cases.case_timeline(con, pick):
                    st.write(f"{ev['ts']} - {ev['actor']} - {ev['event']}")
            with t4:
                if c["status"] == "closed":
                    st.info("This case is closed. Reopen it (next tab) before replying.")
                else:
                    reply_text = st.text_area("Customer reply", key=f"reply{pick}", height=90)
                    b1, b2 = st.columns(2)
                    if b1.button("Add reply and re-plan", key=f"rp{pick}") and reply_text.strip():
                        st.session_state["last_result"] = orchestrator.reply(con, pick, reply_text)
                        st.rerun()
                    if b2.button("Prepare handoff", key=f"ho{pick}"):
                        h = orchestrator.prepare_handoff(con, pick)
                        st.text(h["text"])
                        if h["issues"]:
                            st.warning("Handoff gaps: " + "; ".join(i["detail"] for i in h["issues"]))
            with t5:
                who_c = st.selectbox("Approver", cases.approver_names(), key=f"cw{pick}")
                note_c = st.text_input("Resolution note (required)" if c["status"] != "closed" else "Reason for reopening (required)", key=f"cn{pick}")
                try:
                    if c["status"] != "closed":
                        pend_n = len(cases.list_pending(con, pick))
                        if pend_n:
                            st.warning(f"{pend_n} request(s) still waiting for approval on this case will be CANCELLED (never applied).")
                        if st.button("Close case", key=f"cc{pick}", type="primary"):
                            cases.close_case(con, pick, approver=who_c, note=note_c)
                            st.rerun()
                    elif st.button("Reopen case", key=f"cr{pick}"):
                        cases.reopen_case(con, pick, approver=who_c, note=note_c)
                        st.rerun()
                except ValueError as e:
                    st.error(str(e))
    finally:
        con.close()

# ------------------------------------------------------------------------------------------------ Approvals
with approve_tab:
    st.caption("Everything consequential waits here. One authorised approver is enough. Approving applies an internal update only "
               "(case status, owner, next action, tracker); the system never sends anything externally.")
    con = repo.connect()
    try:
        pending = repo.list_pending(con)
        if not pending:
            st.write("No pending approvals.")
        for p in pending:
            pl, kind = p["payload"], p["type"]
            with st.container(border=True):
                st.markdown(f"**Approval #{p['id']} - {kind}**  \nCase `{p.get('case_id')}` | requested by {p.get('requested_by')}")
                crit = pl.get("critic")
                if crit:
                    (st.success if crit["verdict"] == "pass" else st.error)(
                        f"Critic: **{crit['verdict']}** after {crit.get('revisions', 0)} automatic revision(s).")
                    for i in crit.get("issues", []):
                        st.write(f"- [{i['severity']}] {i['check']}: {i['detail']}")
                    if crit.get("handoff_ready") is False:
                        st.warning("Handoff not complete: " + "; ".join(i["detail"] for i in crit.get("handoff_issues", [])))
                edited = None
                if kind == "external_send":
                    draft = st.text_area("Draft reply (edit before approving; approving does NOT send it)", value=pl.get("draft", ""), height=200, key=f"d{p['id']}")
                    edited = {**pl, "draft": draft} if draft != pl.get("draft", "") else None
                elif kind == "escalation":
                    st.write(f"Category **{pl.get('category')}** | suggested owner: {pl.get('owner')} | mode: {pl.get('mode', '-')} | {pl.get('policy_status', '')}")
                    st.write("Customer query:", pl.get("customer_query"))
                    st.write("Ask the customer to collect:", ", ".join(pl.get("collect", []) or []))
                    if pl.get("missing_evidence"):
                        st.warning("Missing evidence: " + ", ".join(pl["missing_evidence"]))
                    if "draft_reply" in pl:
                        d2 = st.text_area("Draft reply", value=pl.get("draft_reply", ""), height=140, key=f"e{p['id']}")
                        edited = {**pl, "draft_reply": d2} if d2 != pl.get("draft_reply", "") else None
                elif kind == "tracker_update":
                    st.write("Proposed tracker changes (old -> new):")
                    for field, ch in pl.get("changes", {}).items():
                        st.write(f"- **{field}**: {ch.get('old') or '(empty)'} -> {ch['new']}")
                elif kind == "handoff":
                    st.text(pl.get("text", ""))
                    to = st.text_input("Assign to (person)", value=pl.get("assign_to") or "", key=f"a{p['id']}")
                    edited = {**pl, "assign_to": to} if to and to != (pl.get("assign_to") or "") else None
                elif kind == "owner_action":
                    st.write(f"Suggested owner role: **{pl.get('owner_role')}** | next action: {pl.get('next_action')} | urgency {pl.get('urgency')}")
                    person = st.text_input("Assign to (person)", key=f"o{p['id']}")
                    edited = {**pl, "owner": person} if person else None
                else:
                    st.json(pl)
                who = st.selectbox("Approver", repo.cases.approver_names(), key=f"who{p['id']}")
                note = st.text_input("Note (required to reject)", key=f"note{p['id']}")
                c1, c2 = st.columns(2)
                try:
                    if c1.button("Approve and apply", key=f"ok{p['id']}", type="primary"):
                        handlers.approve_and_apply(con, p["id"], approver=who, note=note or None, edited_payload=edited)
                        st.rerun()
                    if c2.button("Reject", key=f"no{p['id']}"):
                        handlers.reject(con, p["id"], approver=who, note=note)
                        st.rerun()
                except ValueError as e:
                    st.error(str(e))
                if p.get("case_id"):
                    with st.expander("Case timeline"):
                        for ev in repo.cases.case_timeline(con, p["case_id"]):
                            st.write(f"{ev['ts']} - {ev['actor']} - {ev['event']}")
    finally:
        con.close()
