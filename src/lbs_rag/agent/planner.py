"""Planner: classify the enquiry, find what is missing, recommend escalation, propose ownership. Rules first (fast, auditable, testable).

What it does (project problems 1, 5, 9, 11, 13):
  classify()  -> workflow (sales | support | unclear), category, urgency, service (provider / API)
  extract_facts() -> what the message already tells us (so nothing already known is asked again)
  plan()      -> checklist status, at most `max_questions` targeted questions, escalation recommendation, proposals
  plan_case() -> the same, persisted on a case: history, known facts, trace steps, checkpoint, and PENDING approval requests

The Planner never contacts anyone and never applies anything: every proposal (owner/next action, escalation) is a pending approval
request for one authorised human (see store/cases.py).
"""
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path

import yaml

from ..retrieve.query_analyzer import detect
from ..safety.escalation import check as escalation_check
from ..store import cases
from ..tools.pricing_intent import has_volume

ROOT = Path(__file__).resolve().parents[3]

_I = re.I


def _N(pat: str) -> str:
    """Whole-number match that tolerates trailing punctuation ("a 422, and ...") but not 500,000 or 1.500."""
    return rf"(?<!\d)(?<!\d[,.])(?:{pat})(?!\d)(?![.,]\d)"

# ------------------------------------------------------------------------------------------------ fact detectors (key -> regex)
_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_CITIES = (r"bengaluru|bangalore|mumbai|delhi|ncr|gurugram|gurgaon|noida|pune|hyderabad|chennai|kolkata|ahmedabad|jaipur|lucknow|indore|"
           r"kochi|coimbatore|chandigarh|bhubaneswar|nagpur|surat|visakhapatnam|patna|bhopal|kanpur|goa|kerala|karnataka|maharashtra|"
           r"tamil nadu|gujarat|rajasthan|uttar pradesh|punjab|india|pan-india|nepal|sri lanka|bangladesh|dubai|apac|global|tier-?[123]")
_DETECT = {
    "timestamp": re.compile(rf"\b\d{{1,2}}[:.]\d{{2}}\s*(?:am|pm|ist|utc|gmt)?\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}\s*(?:{_MONTHS})\b|"
                            r"\b(?:yesterday|today|this (?:morning|afternoon|evening)|last night|since (?:the )?(?:morning|evening|afternoon|yesterday|\d)|"
                            r"for the past \d+ (?:minutes?|hours?|days?)|past \d+ (?:minutes?|hours?|days?)|since \w+ \d+)\b", _I),
    "error_response": re.compile(_N(r"[45]\d{2}") + r"|\b[45]xx\b|(?-i:\b[A-Z]{2,}_[A-Z_]{2,}\b)|(?-i:\bNo[A-Z][a-z]+\b)|\b(?:fail\w*|reject\w*|doesn'?t (?:work|return|appear|change)|does not (?:work|return|appear)|never appears?|not (?:working|returning|appearing)|odd results|unexpected|status \w+)\b|\b(?:ZERO_RESULTS|INVALID_REQUEST|OVER_QUERY_LIMIT|OVER_DAILY_LIMIT|"
                                 r"REQUEST_DENIED|UNKNOWN_ERROR|NOT_FOUND|MAX_ROUTE_LENGTH_EXCEEDED|TooBig|NoRoute|InvalidQuery|INVALID_LATLNG|"
                                 r"FAILURE|MAX_ELEMENTS_EXCEEDED)\b|\b(?:error|exception|timed? ?out|rate limit|denied|failed|failure)\b", _I),
    "endpoint_request": re.compile(r"https?://\S+|/[a-z0-9_]+/v\d\S*|\b\w+=[^\s&,;]+", _I),
    "coordinates": re.compile(r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}"),
    "request_ids": re.compile(r"\b(?:request|trace|x-request)[_ -]?id\b\s*[:=]?\s*\S+|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", _I),
    "impact": re.compile(r"\b\d{1,3}\s?%|\ball (?:requests|calls|users)\b|\bevery (?:call|request)\b|\bmost (?:calls|requests)\b|\bproduction\b|"
                         r"\bintermittent|\bsome (?:requests|calls)\b|\bonly (?:some|a few)\b|\bwhole\b.{0,20}\bdown\b", _I),
    "recent_change": re.compile(r"\b(?:after|since|following)\b.{0,40}\b(?:deploy|release|update|upgrade|change|migration|restriction|rotated|plan|billing)\w*|"
                                r"\b(?:started|began) (?:after|when)\b|\bwe (?:changed|added|removed|switched|upgraded|rotated)\b", _I),
    "volume": re.compile(r"(?<![x×*\d,.])\d[\d,.]*\s*(?:m|k|lakh|crore|million|thousand)?\s*(?:requests?|calls?|lookups?|sessions?|elements?|pairs|searches|users|orders|rides|trips)\b", _I),
    "regions": re.compile(rf"\b(?:{_CITIES})\b", _I),
    "budget": re.compile(r"(?:₹|inr|usd|\$|rs\.?)\s?\d|\bbudget\b|\blakhs?\b|\bcrores?\b|\bper month\b.{0,20}\b(?:spend|cost)\b", _I),
    "timeline": re.compile(rf"\b(?:by|before|within|in|from)\s+(?:the\s+)?(?:next |end of |this )?(?:week|month|quarter|q[1-4]|{_MONTHS}|\d+\s*(?:weeks?|months?|days?))\b|"
                           r"\bgo[- ]?live\b|\blaunch\w*\b|\bdeadline\b|\bpilot\b|\bpoc\b|\bnext (?:week|month|quarter)\b", _I),
    "use_case": re.compile(r"\b(?:delivery|ride[- ]?hailing|logistics|fleet|field[- ]?(?:service|sales)|e-?commerce|quick[- ]?commerce|food|marketplace|"
                           r"real[- ]?estate|ambulance|school[- ]?bus|taxi|cab|tracking|store locator|dispatch|last[- ]?mile|courier|tour|agri|pharma|"
                           r"grocery|app|platform|startup|aggregator|rental|checkout|saas|operator)\b", _I),
    "constraints": re.compile(r"\b(?:sla|uptime|data residency|residency|on-?prem\w*|compliance|gdpr|dpdp|latency|offline|self-?host\w*|"
                              r"store (?:the )?results|caching|enterprise|contract|committed)\b", _I),
    "decision_maker": re.compile(r"\b(?:cto|ceo|cfo|founder|co-?founder|head of|vp|director|procurement|decision[- ]?makers?|approver|"
                                 r"our manager|my manager|i am the|we are the)\b", _I),
}

# ------------------------------------------------------------------------------------------------ classification rules
_SUPPORT_CATEGORIES = [   # first match wins
    ("security", re.compile(r"\b(?:leak(?:ed)?|exposed|pushed .{0,40}(?:github|repo)|bot attack|abuse|compromis\w+|stolen)\b", _I)),
    ("outage", re.compile(_N(r"5\d{2}") + r"|\b5xx\b|\b(?:outage|is down|are down|unavailable|degraded|timed? ?out|production)\b", _I)),
    ("rate_limit", re.compile(_N("429") + r"|\brate[- ]?limit\w*|\bquota\b|\bOVER_QUERY_LIMIT\b|\btoo many requests\b|\bthrottl\w+|\bQPS\b", _I)),
    ("auth", re.compile(r"\bREQUEST_DENIED\b|" + _N("40[13]") + r"|\bunauthori[sz]ed\b|\bforbidden\b|\bapi key\b.{0,40}\b(?:invalid|denied|restrict\w+|rejected)\b|\breferer\b", _I)),
    ("billing", re.compile(r"\b(?:invoice|charged|charges|overcharg\w+|payment failed|credits? (?:ran out|exhausted|are exhausted)|balance (?:hit|is|reached) zero)\b|"
                           r"\bbill\w*\b.{0,60}\b(?:higher|doubled|jumped|jump|spike|wrong|unexpected|more than|increas\w+|dispute|overcharg\w+)\b", _I)),
    ("data_quality", re.compile(r"\b(?:wrong|incorrect|inaccurate|misspelt|misspelled|regression|lower than actual|higher than actual|mislabell?ed|off by)\b", _I)),
    ("coverage", re.compile(r"\bZERO_RESULTS\b|no serviceable|\bcoverage\b|\bnot[ _]found\b|\bnoroute\b|\bnosegment\b|\boutside\b.{0,30}\b(?:area|coverage)\b|cross-?border", _I)),
    ("invalid_request", re.compile(_N("400|422") + r"|\bINVALID_REQUEST\b|\bMAX_\w+|\b\w+_EXCEEDED\b|\bTooBig\b|\binvalid\b|\bmalformed\b|\bunrecognis?ed\b|\bmissing\b.{0,20}\bparameter", _I)),
    ("performance", re.compile(r"\b(?:slow\w*|latency|takes? (?:too )?long|laggy)\b", _I)),
    # generic failure language ("fails", "rejects", "never appears", "comes back in English", "odd results", "isn't", "why ...")
    ("troubleshooting", re.compile(r"\b(?:fail\w*|reject\w*|doesn'?t|does not|didn'?t|did not|isn'?t|aren'?t|never|not (?:working|returning|appearing|showing)|"
                                   r"odd|unexpected|ignored|comes? back|come back|stopped|stops (?:working|responding)|status \w+|why|nothing|blocked|not authori[sz]ed|incorrect\w*|"
                                   r"still (?:see|get|shows?|returns?)|only seems|wrong)\b", _I)),
    ("how_to", re.compile(r"\bhow (?:do|can|should|to)\b|\bwhich parameter\b|\bis it possible\b|\bcan (?:we|i)\b|\bwhat (?:happens|is the)\b", _I)),
]
_SALES_CATEGORIES = [
    ("contract", re.compile(r"\b(?:sla|negotiat\w+|committed|committed-use|on-?prem\w*|data residency|enterprise agreement|custom (?:plan|pricing)|discount)\b", _I)),
    ("competitive", re.compile(r"\b(?:vs\.?|versus|compare|comparison|better than|alternative to)\b", _I)),
    ("migration", re.compile(r"\b(?:migrat\w+|switch(?:ing)? from|move from|replace)\b", _I)),
    ("licensing", re.compile(r"\blicen[cs](?:e|es|ing|ed)\b|\bterms of (?:use|service)\b|\bcommercial (?:use|app\w*)\b|\ballowed to use\b|\bobligations?\b", _I)),
    ("pricing", re.compile(r"\b(?:price|prices|pricing|cost|costs|quote|estimate|how much|free|allowance|cheap(?:er|est)|budget|rate|rates|sku)\b|per 1,?000", _I)),
    ("discovery", re.compile(r"\b(?:what do you recommend|recommend|want a|need a|looking for|we'?re building|we are building|we plan|should (?:we|i) use)\b", _I)),
    ("capability_match", re.compile(r"\b(?:support|supports|does it|can it|capabilit\w+|do you have|feature)\b|\bwhich (?:\w+[ /-]){0,3}(?:apis?|providers?|geocoders?|services?)\b", _I)),
]
_SALES_RX = dict(_SALES_CATEGORIES)


def mentions_pricing(message: str) -> bool:
    """True if the message asks about price / cost / rate / free allowance (used by the router: priced questions go to the calculator)."""
    return bool(_SALES_RX["pricing"].search(message))


_SUPPORT_CATS = {c for c, _ in _SUPPORT_CATEGORIES}
_SALES_CATS = {c for c, _ in _SALES_CATEGORIES}
_HIGH_URGENCY = re.compile(r"\b(?:urgent|asap|immediately|production|outage|down|all (?:requests|calls)|every (?:call|request)|security|leaked|breach)\b", _I)


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config" / "planner.yaml").read_text(encoding="utf-8"))


def classify(message: str, *, known_workflow: str | None = None, known_category: str | None = None) -> dict:
    """-> {workflow, category, urgency, service: {providers, apis}, signals}
    A follow-up reply ("it started at 14:05") carries no category signal, so the case's established workflow and category are kept
    unless the new message gives a clearly different one."""
    support_cat = next((c for c, rx in _SUPPORT_CATEGORIES if rx.search(message)), None)
    sales_cat = next((c for c, rx in _SALES_CATEGORIES if rx.search(message)), None)
    has_err = bool(_DETECT["error_response"].search(message)) and support_cat != "how_to"
    # Strong support signals (errors, outages, incidents, billing disputes) beat sales signals. 'how_to' and bare
    # 'capability' words are weak and only decide the workflow when nothing else does.
    strong_support = support_cat in {"security", "outage", "rate_limit", "auth", "billing", "data_quality", "coverage", "invalid_request",
                                     "performance", "troubleshooting"} or has_err
    strong_sales = sales_cat in {"contract", "competitive", "migration", "pricing", "discovery", "licensing", "capability_match"}
    if strong_support and not (strong_sales and sales_cat in {"contract"} and support_cat in {None, "billing"}):
        workflow, category = "support", support_cat or ("troubleshooting" if has_err else "how_to")
    elif strong_sales:
        workflow, category = "sales", sales_cat
    elif support_cat == "how_to":
        workflow, category = "support", "how_to"
    elif sales_cat:
        workflow, category = "sales", sales_cat
    elif known_workflow in ("sales", "support"):
        workflow, category = known_workflow, (known_category or ("how_to" if known_workflow == "support" else "discovery"))
    else:
        workflow, category = "unclear", None
    weak = {"how_to", "troubleshooting"}
    if category in weak and known_workflow == workflow and known_category not in (None, *weak):
        category = known_category          # a weak "can we ..." / "why ..." reply must not downgrade an established category
    low = category in ("how_to", "pricing", "capability_match", "competitive")
    urgency = "high" if _HIGH_URGENCY.search(message) and workflow == "support" else ("low" if low else "normal")
    d = detect(message)
    return {"workflow": workflow, "category": category, "urgency": urgency,
            "service": {"providers": d["providers"], "apis": d["apis"]},
            "signals": {"support_category": support_cat, "sales_category": sales_cat}}


def extract_facts(message: str) -> dict[str, str]:
    """What this message already tells us, keyed by checklist item. Values are the matched snippets (short)."""
    facts: dict[str, str] = {}
    for key, rx in _DETECT.items():
        if key == "volume":
            m = rx.search(message)
            if m:
                facts[key] = m.group(0).strip()
            elif has_volume(message):
                facts[key] = "volume stated"
            continue
        m = rx.search(message)
        if m:
            facts[key] = message[m.start():m.end()].strip()[:80]
    d = detect(message)
    if d["providers"]:
        facts["provider"] = ", ".join(d["providers"])
    if d["apis"]:
        facts["api"] = ", ".join(d["apis"])
        facts["api_needs"] = facts["api"]
    if d["providers"]:                       # naming the provider is enough to know where to look; the API is a bonus
        facts["provider_api"] = f"{facts['provider']} / {facts['api']}" if d["apis"] else facts["provider"]
    return facts


def _checklist(workflow: str, category: str | None) -> list[dict]:
    items = config().get(workflow, [])
    return [i for i in items if not i.get("categories") or category in i["categories"]]


def plan(message: str, *, known_facts: dict[str, str] | None = None, known_workflow: str | None = None,
         known_category: str | None = None, asked_counts: dict[str, int] | None = None) -> dict:
    """Pure planning (no database). `known_facts` come from the case history; `asked_counts` says how often each item was already asked."""
    t0 = time.time()
    steps: list[dict] = []
    cls = classify(message, known_workflow=known_workflow, known_category=known_category)
    steps.append({"step": "classify", "ms": int((time.time() - t0) * 1000), "output": {k: cls[k] for k in ("workflow", "category", "urgency")}})
    new = extract_facts(message)
    merged = {**(known_facts or {}), **new}
    steps.append({"step": "extract_facts", "output": {"new": sorted(new), "known_before": sorted(known_facts or {})}})

    cfg, asked = config(), (asked_counts or {})
    checklist, missing_required, missing_optional, need_human = [], [], [], []
    if cls["workflow"] in ("sales", "support"):
        for it in _checklist(cls["workflow"], cls["category"]):
            present = it["key"] in merged
            row = {"key": it["key"], "label": it["label"], "required": it["required"], "status": "present" if present else "missing",
                   "value": merged.get(it["key"])}
            checklist.append(row)
            if present:
                continue
            if asked.get(it["key"], 0) >= cfg["max_times_asked"]:
                need_human.append(it["key"])               # asked enough times: a person should follow up instead
            else:
                (missing_required if it["required"] else missing_optional).append(it)
    ordered = missing_required + missing_optional
    questions = [{"key": i["key"], "question": i["question"], "required": i["required"]} for i in ordered[:cfg["max_questions"]]]
    steps.append({"step": "gap_analysis", "output": {"missing_required": [i["key"] for i in missing_required],
                                                      "questions": [q["key"] for q in questions], "needs_human": need_human}})

    # escalation recommendation (evidence-backed; PROPOSED policy until the approved source exists)
    esc_rule = escalation_check(message)
    escalation = {"recommend": False, "policy_status": "proposed; approved policy source (SC-02) not supplied"}
    if esc_rule["escalate"]:
        cat = esc_rule["category"]
        need = cfg["escalation"]["evidence_needed"].get(cat, [])
        missing_ev = [k for k in need if k not in merged]
        escalation.update({"recommend": True, "category": cat, "owner": esc_rule["owner"], "collect": esc_rule["collect"],
                           "mode": cfg["escalation"]["mode"].get(cat, "now"),
                           "evidence": {k: merged[k] for k in need if k in merged}, "missing_evidence": missing_ev,
                           "matched": esc_rule["matched"]})
    steps.append({"step": "escalation_check", "output": {"recommend": escalation["recommend"], "category": escalation.get("category")}})

    proposals = []
    owner_role = cfg["owner_roles"].get(cls["category"]) if cls["category"] else None
    if owner_role:
        nxt = ("send the questions above to the customer" if questions else
               "proceed to investigation / drafting" if cls["workflow"] != "unclear" else "clarify the request")
        proposals.append({"type": "owner_action", "payload": {"owner_role": owner_role, "next_action": nxt, "category": cls["category"],
                                                              "workflow": cls["workflow"], "urgency": cls["urgency"]}})
    if escalation["recommend"]:
        proposals.append({"type": "escalation", "payload": {k: escalation.get(k) for k in
                          ("category", "owner", "collect", "mode", "evidence", "missing_evidence", "policy_status")} | {"customer_query": message}})
    return {**cls, "known_facts": merged, "checklist": checklist, "missing_required": [i["key"] for i in missing_required],
            "questions": questions, "needs_human_followup": need_human,
            "ready_to_proceed": cls["workflow"] != "unclear" and not missing_required and not need_human,
            "escalation": escalation, "proposals": proposals, "steps": steps}


def plan_case(con, case_id: str, message: str, *, submit: bool = True) -> dict:
    """Plan on a persisted case: log the message, update known facts (so nothing is re-asked), save the checkpoint, trace each step,
    record the questions asked, and (if submit) create PENDING approval requests for the proposals (once each)."""
    case = cases.get_case(con, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")
    trace_id = uuid.uuid4().hex[:12]
    msg_id = cases.add_message(con, case_id, "customer", message)
    prior = cases.known_facts(con, case_id)
    state = cases.load_state(con, case_id)
    asked = dict(state.get("asked_counts", {}))
    t0 = time.time()
    result = plan(message, known_facts=prior, known_workflow=case.get("workflow") if case.get("workflow") != "unclear" else None,
                  known_category=case.get("category"), asked_counts=asked)
    new_keys = [k for k in result["known_facts"] if k not in prior or prior[k] != result["known_facts"][k]]
    for k in new_keys:
        cases.set_fact(con, case_id, k, result["known_facts"][k], msg_id)
    fields = {"workflow": result["workflow"], "category": result["category"], "urgency": result["urgency"],
              "provider": (result["service"]["providers"] or [None])[0], "api": (result["service"]["apis"] or [None])[0]}
    cases.update_case(con, case_id, actor="planner", **{k: v for k, v in fields.items() if v is not None})   # never blank out known values
    for s in result["steps"]:
        cases.trace_step(con, trace_id, s["step"], "planner", {"message": message[:300]}, s["output"], t0, case_id=case_id)

    for q in result["questions"]:
        asked[q["key"]] = asked.get(q["key"], 0) + 1
    if result["questions"]:
        text = "To move forward I need:\n" + "\n".join(f"- {q['question']}" for q in result["questions"])
        cases.add_message(con, case_id, "agent", text)
    state.update({"step": "awaiting_customer" if result["questions"] else "planned", "asked_counts": asked,
                  "last_trace_id": trace_id, "ready_to_proceed": result["ready_to_proceed"],
                  "last_plan": {"workflow": result["workflow"], "category": result["category"], "urgency": result["urgency"],
                                "missing_required": result["missing_required"], "needs_human_followup": result["needs_human_followup"],
                                "questions": result["questions"]}})
    cases.save_state(con, case_id, state)            # update, never replace: Executor / Critic results already on the case must survive

    submitted = []
    if submit:
        existing = {(a["type"], a["case_id"]) for a in cases.list_pending(con, case_id)}
        for p in result["proposals"]:
            if (p["type"], case_id) in existing:
                continue                                                        # never duplicate a pending request
            submitted.append(cases.request_approval(con, case_id, p["type"], p["payload"], requested_by="planner"))
    return {**result, "case_id": case_id, "trace_id": trace_id, "submitted_approvals": submitted}
