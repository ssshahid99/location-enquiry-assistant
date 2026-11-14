"""Demo UI. Run from the project root:
    $env:PYTHONPATH=".;src"; streamlit run app/streamlit_app.py
Tabs: Chat (one Planner picks llm | rag | escalation per message) | Direct answer | Case workflow (older agent) | Approvals (one authorised human decides,
with a decision log and the sandbox records) | Traces.
Nothing is ever sent externally: approving applies an internal update only (case status, owner, next action, tracker)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import streamlit as st  # noqa: E402

from lbs_rag.agent import orchestrator  # noqa: E402
from lbs_rag.chat import sandbox as sandbox_store  # noqa: E402
from lbs_rag.chat import state as conv_state  # noqa: E402
from lbs_rag.chat.chat import chat as chat_turn  # noqa: E402
from lbs_rag.generate import meta  # noqa: E402
from lbs_rag.generate.answer import answer  # noqa: E402
from lbs_rag.safety.pii import redact_with_report  # noqa: E402
from lbs_rag.store import cases, customers, handlers, review, traces  # noqa: E402
from lbs_rag.store import repositories as repo  # noqa: E402

st.set_page_config(page_title="Location Services Copilot", layout="wide")
st.title("Location Services Sales & Support Copilot")

try:
    from lbs_rag.ingest.freshness import snapshot_info
    _snap = snapshot_info()
    if _snap["age_days"] is not None:
        _txt = f"Documentation snapshot: pages fetched {_snap['oldest']} to {_snap['newest']} (oldest is {_snap['age_days']} days old)."
        (st.warning if _snap["stale"] else st.caption)(_txt + (" Prices and limits may have changed: refresh the snapshot." if _snap["stale"] else ""))
except Exception:  # noqa: BLE001 - never block the app on this banner
    pass

st.sidebar.header("What can I ask?")
st.sidebar.caption("Click an example to fill the question box, or type 'what can you do?'.")
for _ex in meta.EXAMPLES:
    if st.sidebar.button(_ex, key="ex_" + _ex[:30]):
        st.session_state["q"] = _ex
        st.session_state["case_msg"] = _ex
        st.session_state["chat_example"] = _ex          # the Chat tab sends it as the next message

chat_tab, ask_tab, cases_tab, approve_tab, trace_tab = st.tabs(["Chat", "Direct answer", "Case workflow (older)", "Approvals", "Traces"])


def show_trace(con, trace_id: str):
    """Steps and sources behind one answer: what was asked, which passages the model saw (cited ones marked), tool results, steps."""
    t = traces.get_trace(con, trace_id)
    if t is None:
        st.warning("Trace not found.")
        return
    st.write(f"Trace `{t['id']}` | {t.get('ts', '')} | reason: {t.get('reason') or '-'} | model calls: "
             f"{t['model_calls'] if t.get('model_calls') is not None else 'not recorded'}")
    if t.get("query"):
        st.markdown(f"**Question:** {t['query']}")
        st.markdown(f"**Answer:** {t['answer']}")
    if t["passages"]:
        st.markdown("**Passages the model was shown** (cited ones are marked)")
        for p in t["passages"]:
            with st.expander(f"{'CITED ' if p['cited'] else ''}#{p['rank']} {p['provider'] or ''} - {p['section'] or ''}"):
                st.markdown(f"[{p['doc_url']}]({p['doc_url']})")
                st.text(p["snippet"])
    for tc in t["tool_calls"]:
        with st.expander(f"Tool: {tc.get('tool')}"):
            st.json(tc, expanded=False)
    if t["steps"]:
        st.markdown("**Execution steps**")
        for s in t["steps"]:
            st.write(f"{s['component']} / {s['step']} ({s['ms'] or 0} ms)")
    if t.get("case_id"):
        st.caption(f"Belongs to case {t['case_id']}")

ROUTE_LABEL = {"rag": "Answered directly from documentation", "agent": "Handled as a case by the agent (Planner, Executor, Critic)",
               "human": "Needs a person: case and handoff prepared", "refused": "Not processed (scope or safety rule)"}


def show_sources(citations):
    if citations:
        st.subheader("Sources")
        for c in citations:
            st.markdown(f"**[{c['n']}]** {c['provider']} - [{c['doc_url']}]({c['doc_url']})  \n"
                        f"<small>fetched {c.get('fetched_at', '')}</small>", unsafe_allow_html=True)


# ------------------------------------------------------------------------------------------------ Chat
ROUTE_BADGE = {"llm": "LLM: conversation only", "rag": "RAG: documents and calculator", "escalation": "Escalation: a person must approve",
               "refuse": "Not processed (scope or safety rule)"}
with chat_tab:
    st.caption("One conversation. For every message the Planner picks ONE route: LLM (conversation), RAG (documents and the calculator) or "
               "Escalation (a person). Escalations and record updates are only proposed; they wait in the Approvals tab.")
    hc1, hc2 = st.columns([4, 1])
    chat_customer = hc2.text_input("Customer", value="Demo customer", key="chat_customer")
    if hc2.button("New conversation"):
        st.session_state.pop("conv_id", None)
        st.session_state["chat_log"] = []
    log = st.session_state.setdefault("chat_log", [])
    new_msg = st.chat_input("Type the customer's message (e.g. 'Ola Directions free tier?', 'Hello', 'I want to talk to a person')")
    new_msg = new_msg or st.session_state.pop("chat_example", None)          # an example clicked in the sidebar is sent as a message
    if new_msg:
        with st.spinner("Planner is choosing a route..."):
            _c = repo.connect()
            try:
                _r = chat_turn(_c, new_msg, st.session_state.get("conv_id"), customer=chat_customer)
            finally:
                _c.close()
        st.session_state["conv_id"] = _r["conv_id"]
        log.append({"user": new_msg, "r": _r})
    if st.session_state.get("conv_id"):
        hc1.caption(f"Conversation `{st.session_state['conv_id']}` (state is saved: facts, questions asked, summary)")
        _sc = repo.connect()
        try:
            _st = conv_state.load(_sc, st.session_state["conv_id"])
        finally:
            _sc.close()
        with st.expander("What the system remembers about this conversation", expanded=False):
            st.write("**Summary:**", _st["summary"] or "(nothing yet)")
            st.write("**Known facts:**", _st["facts"] or "(none)")
            if _st["asked"]:
                st.write("**Questions already asked:**")
                for _f, _a in _st["asked"].items():
                    st.write(f"- {'answered' if _a['answered'] else 'waiting'}: {_a['question']}")
            if _st["pending"]:
                st.write("**Original question waiting for those details:**", _st["pending"]["question"])
    if log:
        _routes = [t["r"]["route"] for t in log]
        st.caption("Routes this conversation: " + ", ".join(f"{k} x{_routes.count(k)}" for k in dict.fromkeys(_routes)))
    for turn in log:
        with st.chat_message("user"):
            st.markdown(turn["user"])
        r = turn["r"]
        with st.chat_message("assistant"):
            st.caption(f"{ROUTE_BADGE.get(r['route'], r['route'])}  |  why: {r['route_reason']}  |  decided by: {r['route_source']}")
            st.markdown(r["text"])
            show_sources(r.get("citations", []))
            if r.get("action_id") or r.get("record_action_id"):
                st.warning("Waiting for human approval in the Approvals tab. Nothing has been sent or changed.")
            _pru = r["outputs"]["proposed_record_update"]
            if _pru["applicable"]:
                st.markdown(f"**Proposed update to `{_pru['proposal']['record_id']}`** (pending approval)")
                st.table([{"field": k, "proposed value": v} for k, v in _pru["proposal"]["fields"].items()])
            _rl = r["outputs"]["responsible_role"]
            st.caption(f"Owner: {_rl['owner']} ({_rl['team']}) | severity {_rl['severity']} | escalate: {'yes' if _rl['should_escalate'] else 'no'}")
            _qs = r["outputs"]["clarifying_questions"]
            if _qs["needed"]:
                st.info("Questions asked: " + " | ".join(q["text"] for q in _qs["questions"]))
            if r["schema_errors"]:
                st.error("Output failed schema validation: " + "; ".join(r["schema_errors"][:3]))
            with st.expander("Structured outputs (the 7 schema-valid outputs)"):
                st.json(r["outputs"], expanded=False)
            with st.expander("Tool calls and model calls"):
                st.write(f"Model calls this turn: {r['model_calls']}")
                st.json(r["tool_calls"], expanded=False)

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
        with st.expander("Trace (steps and sources)"):
            _c = repo.connect()
            try:
                show_trace(_c, a["trace_id"])
            finally:
                _c.close()

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
            rv = review.review_case(con, pick)
            t7, t1, t2, t3, t4, t5, t6 = st.tabs([f"Review all ({rv['count']})", "Conversation", "Known facts and tracker", "Timeline",
                                                  "Reply / handoff", "Close / reopen", "Trace"])
            with t7:
                if not rv["items"]:
                    st.write("Nothing is waiting for approval on this case.")
                else:
                    st.caption("Every request is still decided and recorded on its own; this screen only saves the clicking. Nothing is sent externally, "
                               "and nothing is approved unless you tick it.")
                    if rv["stale_count"]:
                        st.warning(f"{rv['stale_count']} request(s) are stale: the customer wrote again after they were prepared.")
                    picks = {}
                    for it in rv["items"]:
                        with st.container(border=True):
                            st.markdown(f"**#{it['id']} {it['type']}**" + ("  :red[STALE]" if it["stale"] else ""))
                            for w in it["warnings"]:
                                st.write(f"- :orange[{w}]")
                            pl = it["payload"]
                            st.write(pl.get("draft") or pl.get("draft_reply") or pl.get("text") or pl.get("changes") or
                                     {k: pl[k] for k in ("owner_role", "next_action", "urgency", "category", "owner") if k in pl})
                            choice = st.radio("Decision", ["leave pending", "approve", "reject"], horizontal=True, key=f"rv{it['id']}")
                            ack = st.checkbox("I have re-checked this stale request", key=f"rvack{it['id']}") if it["stale"] else False
                            picks[it["id"]] = (choice, ack)
                    who_r = st.selectbox("Approver", cases.approver_names(), key=f"rvwho{pick}")
                    note_r = st.text_input("Note (required for every rejection)", key=f"rvnote{pick}")
                    if st.button("Apply my decisions", type="primary", key=f"rvgo{pick}"):
                        decs = [{"action_id": i, "approve": ch == "approve", "acknowledge_stale": ack, "note": note_r or None}
                                for i, (ch, ack) in picks.items() if ch != "leave pending"]
                        if not decs:
                            st.info("Nothing selected.")
                        else:
                            try:
                                res = review.decide_bundle(con, pick, decs, approver=who_r)
                                for r in res["results"]:
                                    (st.success if r["ok"] else st.error)(f"#{r['action_id']} {r['type']}: " + (r["status"] if r["ok"] else r["error"]))
                                if not res["failed"]:
                                    st.rerun()
                            except ValueError as e:
                                st.error(str(e))
            with t6:
                ct = traces.case_trace(con, pick)
                if not ct["steps"]:
                    st.write("No recorded steps for this case.")
                for tid, steps in ct["by_message"].items():
                    with st.expander(f"Message {tid}: " + " > ".join(f"{s['component']}/{s['step']}" for s in steps)):
                        show_trace(con, tid)
                st.caption(f"Total step time {ct['total_step_ms']} ms | {len(ct['approvals'])} approval request(s)")
            with t1:
                for m in cases.get_case_history(con, pick, limit=100):
                    st.markdown(f"**{m['role']}** ({m['ts']}): {m['content']}")
            with t2:
                st.write("Known facts (working memory):", cases.known_facts(con, pick))
                st.write("Approved tracker:", cases.get_tracker(con, pick) or "nothing approved yet")
                hist = customers.summary(con, pick)
                if hist["earlier_cases"]:
                    st.markdown("**Earlier cases of this customer**")
                    for ec in hist["earlier_cases"]:
                        st.write(f"- `{ec['id']}` {ec['created_at'][:10]} | {ec['status']} | {ec['category'] or ec['workflow']} | {ec['next_action'] or ''}")
                if hist["unconfirmed_facts"]:
                    st.markdown("**From earlier cases, NOT yet confirmed** (not used until you confirm that they still apply)")
                    who_f = st.selectbox("Confirmed by", cases.approver_names(), key=f"pfwho{pick}")
                    for pf in hist["unconfirmed_facts"]:
                        with st.container(border=True):
                            st.write(f"**{pf['key']}**: {pf['value']}  \n<small>said on case {pf['from_case']}, {pf['from_date'][:10]}</small>", unsafe_allow_html=True)
                            newv = st.text_input("Corrected value (leave as is if still true)", value=pf["value"], key=f"pfv{pick}{pf['key']}")
                            b1, b2 = st.columns(2)
                            if b1.button("Still true", key=f"pfok{pick}{pf['key']}"):
                                customers.confirm_prior_fact(con, pick, pf["key"], who_f, value=newv)
                                st.rerun()
                            if b2.button("No longer applies", key=f"pfno{pick}{pf['key']}"):
                                customers.dismiss_prior_fact(con, pick, pf["key"], who_f)
                                st.rerun()
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
    st.caption("Everything consequential waits here: escalations, sandbox record updates, replies. One authorised approver is enough, and every "
               "decision is logged below. Approving applies an internal update only (case status, owner, next action, tracker, sandbox record); "
               "the system never sends anything externally.")
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
                    st.write(f"Category **{pl.get('category')}** | suggested owner: {pl.get('owner')} | severity {pl.get('severity', '-')} | team {pl.get('owner_team', '-')} | mode: {pl.get('mode', '-')} | {pl.get('policy_status', '')}")
                    st.write("Customer query:", pl.get("customer_query"))
                    st.write("Ask the customer to collect:", ", ".join(pl.get("collect", []) or []))
                    if pl.get("missing_evidence"):
                        st.warning("Missing evidence: " + ", ".join(pl["missing_evidence"]))
                    if "draft_reply" in pl:
                        d2 = st.text_area("Draft reply", value=pl.get("draft_reply", ""), height=140, key=f"e{p['id']}")
                        edited = {**pl, "draft_reply": d2} if d2 != pl.get("draft_reply", "") else None
                elif kind == "record_update":
                    st.write(f"Sandbox record **{pl.get('record_id')}**: proposed field-level changes (old -> new). Nothing is written until you approve.")
                    new_fields = {}
                    for field, val in pl.get("fields", {}).items():
                        old = (pl.get("diff", {}).get(field) or {}).get("old")
                        new_fields[field] = st.text_input(f"{field} (was: {old if old is not None else '(empty)'})", value=str(val), key=f"rf{p['id']}{field}")
                    edited = {**pl, "fields": new_fields} if new_fields != {k: str(v) for k, v in pl.get("fields", {}).items()} else None
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
        with st.expander("Decision log (who decided what, when, and the final change)"):
            _dl = repo.cases.decision_log(con, limit=100)
            if not _dl:
                st.write("No decisions yet.")
            else:
                st.dataframe([{"action": d["action_id"], "type": d["type"], "decision": d["decision"], "reviewer": d["reviewer"],
                               "decided at": d["decided_at"], "edited by reviewer": d["edited"], "note": d["note"] or "",
                               "applied diff": str(d["final_diff"]) if d["final_diff"] else ""} for d in _dl], use_container_width=True)
        with st.expander("Sandbox records (changed only by approved updates)"):
            _rows = con.execute("SELECT record_id, fields_json, updated_at, last_action_id FROM sandbox_records ORDER BY updated_at DESC").fetchall()
            if not _rows:
                st.write("No sandbox record has been changed yet.")
            else:
                st.dataframe([{"record": r_["record_id"], "fields": r_["fields_json"], "updated": r_["updated_at"], "by action": r_["last_action_id"]}
                              for r_ in _rows], use_container_width=True)
    finally:
        con.close()

# ------------------------------------------------------------------------------------------------ Traces
with trace_tab:
    st.caption("Review any answer: the question, the passages the model was shown (cited ones marked), tool results and steps. Read-only.")
    f1, f2 = st.columns(2)
    only = f1.selectbox("Show", ["all"] + list(traces.FILTERS), key="tr_only")
    needle = f2.text_input("Question contains", key="tr_q")
    con = repo.connect()
    try:
        rows = traces.list_traces(con, only=None if only == "all" else only, query_contains=needle or None, limit=100)
        if not rows:
            st.write("No traces yet.")
        else:
            labels = {f"{r['ts'][:19]} | {r['reason'] or 'answered'} | {r['query'][:70]}": r["id"] for r in rows}
            show_trace(con, labels[st.selectbox("Trace", list(labels), key="tr_pick")])
    finally:
        con.close()