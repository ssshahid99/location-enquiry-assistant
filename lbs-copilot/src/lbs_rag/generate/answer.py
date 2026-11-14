"""answer(query) -> {text, citations[], tool_calls[], abstained, escalate, reason, trace_id, proposed_action_id}

Pipeline: escalation check -> retrieve -> (if pricing) extract workload + deterministic calculator -> grounded generation
(Qwen3-8B) -> trace to SQLite. An escalation never contacts anyone: it creates a *pending proposed action* that a human
must approve (project rule). Set persist=False for evals so they don't write traces or proposed actions.
"""
import re
import uuid

from config.settings import count_model_calls, openai_client, settings

from ..retrieve.hybrid import retrieve
from ..safety import escalation
from ..safety.pii import PUBLIC_EMAILS, redact_with_report
from ..store import repositories as repo
from ..tools import facts, free_tier, pricing_intent, rate_card
from . import grounding, guard, meta
from .abstain import looks_abstained, pricing_blocked, unsupported_providers
from .prompts import SYSTEM, build_user

TOP_K = 6
NO_ANSWER = ("I couldn't produce a reliable answer to this from my verified sources. Please rephrase, or ask about pricing, limits, "
             "errors or policies for the supported providers. Type \"what can you do?\" for examples.")
_SELF_REFUSAL = re.compile(
    r"(outside|beyond) (of )?(the |my )?(supported |current )?scope|not (related|relevant) to (the )?(supported )?(location|mapping|api)|"
    r"(isn'?t|is not) (about|related to) (the )?(supported )?(location|mapping)|not something this system|"
    r"cannot provide information (on|about) (how|what)", re.I)


def _clean(s: str) -> str:
    return re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()


def _add_price_sources(hits: list[dict], pricing: dict) -> list[int]:
    """Make sure the list-price page of every priced provider is among the cited sources. Returns their 1-based indices."""
    if not (pricing.get("pricing") and pricing["results"]):
        return []
    idx = []
    for prov in dict.fromkeys(r["provider"] for r in pricing["results"]):
        cand = [h for h in hits if h["provider"] == prov and "pric" in h["doc_url"]]
        if not cand:
            cand = [h for h in retrieve(f"{prov} pricing free usage per 1,000 requests rate tiers", k=6, filters={"provider": prov})
                    if "pric" in h["doc_url"]][:1]
            for h in cand:
                if h["chunk_id"] not in {x["chunk_id"] for x in hits}:
                    hits.append(h)
        if cand:
            idx.append(next(i for i, h in enumerate(hits, 1) if h["chunk_id"] == cand[0]["chunk_id"]))
    return idx


def _finish(query: str, out: dict, esc: dict, persist: bool) -> dict:
    out.setdefault("proposed_action_id", None)
    out.setdefault("refused", False)
    # defence in depth: never let a secret/PII-looking string leave in an answer, whatever the model produced
    out["text"], _found = redact_with_report(out["text"], allow_emails=PUBLIC_EMAILS)
    if persist:
        con = repo.connect()
        try:
            if esc["escalate"]:
                # an escalation opens a CASE (type 'incident'), logs the exchange on it, and asks ONE human to approve
                urgent = esc["category"] in ("production_incident", "security_incident", "billing_incident")
                cid = repo.cases.create_case(con, workflow="support", type="incident", category=esc["category"],
                                             urgency="high" if urgent else "normal")
                repo.cases.add_message(con, cid, "customer", query)
                repo.cases.add_message(con, cid, "agent", out["text"])
                out["case_id"] = cid
                out["proposed_action_id"] = repo.create_proposed_action(con, type="escalation", case_id=cid, payload={
                    "trace_id": out["trace_id"], "category": esc["category"], "owner": esc["owner"],
                    "collect": esc["collect"], "customer_query": query, "draft_reply": out["text"]})
            repo.save_trace(con, trace_id=out["trace_id"], query=query, a=out, model=settings.chat_model)
        finally:
            con.close()
    return out


def answer(query: str, **kw) -> dict:
    """Grounded answer. The result also carries `model_calls`: how many model requests this answer really made (measured)."""
    with count_model_calls() as calls:
        out = _answer(query, **kw)
    out["model_calls"] = calls.count
    if kw.get("persist", True) and out.get("trace_id"):
        repo.set_trace_model_calls(out["trace_id"], calls.count)
    return out


def _answer(query: str, *, k: int = TOP_K, persist: bool = True, rewrite: bool = True, route: bool = True,
            cover: bool = True, **retrieval_opts) -> dict:
    """rewrite=True is the default: on the rebuilt corpus it raised cited-passage recall@6 by ~8 points and citation
    support from 74% to 90% (60-question A/B); re-ranking hurt and k=8 did not help, so both stay off."""
    trace_id = uuid.uuid4().hex[:12]
    esc = escalation.check(query)
    g = guard.check(query)             # scope + ethics screen BEFORE anything else: refused requests never reach retrieval or the model
    if g["refuse"]:
        return _finish(query, {"text": g["message"], "citations": [], "tool_calls": [], "abstained": True, "refused": True,
                               "escalate": False, "reason": f"refused:{g['category']}", "trace_id": trace_id, "retrieved": []},
                       {"escalate": False}, persist)
    gen = meta.respond(query)          # greetings / "what can you do" / scope / limits: from config, no retrieval or model
    if gen:
        return _finish(query, {"text": gen[0], "citations": [], "tool_calls": [], "abstained": False, "escalate": False,
                               "reason": f"general:{gen[1]}", "trace_id": trace_id, "retrieved": []}, esc, persist)
    oos, has_known = unsupported_providers(query)
    if oos and not has_known:   # only providers outside the knowledge base: don't retrieve-and-improvise
        return _finish(query, {
            "text": f"I don't have verified information about {', '.join(oos)}. This copilot covers Google Maps Platform, "
                    "Mapbox, HERE, Ola Maps, Mappls (MapmyIndia), OSM Nominatim and OSRM. For other providers, please check the vendor's own "
                    "documentation or ask the vendor for a quote.",
            "citations": [], "tool_calls": [], "abstained": True, "escalate": False,
            "reason": f"out_of_scope_provider: {', '.join(oos)}", "trace_id": trace_id, "retrieved": []}, esc, persist)
    # route=True (provider-aware extra searches): cited-passage recall@6 38.8% -> 48.0% without rewrite, 40.8% -> 46.9% with it
    if not guard.has_strong_domain_term(query):
        # off-topic gate: no domain term, and either an obviously unrelated subject or no resemblance to our corpus
        sim = None
        if guard.DOMAIN_SIM_MIN is not None:
            from ..retrieve.hybrid import dense_search
            top = dense_search(query, n=1)
            sim = top[0]["score"] if top else 0.0
        off = guard.domain_gate(query, sim)
        if not off and sim is not None and sim < guard.BORDERLINE_SIM and not guard.llm_in_scope(query):
            off = {"refuse": True, "category": "off_topic", "message": guard.off_topic_message()}
        if off:
            return _finish(query, {"text": off["message"], "citations": [], "tool_calls": [], "abstained": True, "refused": True,
                                   "escalate": False, "reason": f"refused:{off['category']}", "trace_id": trace_id,
                                   "retrieved": []}, {"escalate": False}, persist)
    hits = retrieve(query, k=k, rewrite=rewrite, route=route, cover=cover, **retrieval_opts)
    pricing = pricing_intent.run(query)
    source_idx = _add_price_sources(hits, pricing)
    fact_lines = facts.attach_facts(hits, facts.matching_facts(query))
    tool_calls = [{"tool": "pricing_calc", "input": pricing["extracted"], "output": pricing["results"],
                   "unverified": pricing["unverified"], "errors": pricing["errors"]}] if pricing["pricing"] else []

    no_answer = model_refused = ungrounded = False
    removed_claims: list[str] = []
    removed_why: list[str] = []
    force_cites: list[int] = []
    ft = free_tier.answer_text(query) if not esc["escalate"] else None
    rc = rate_card.answer_text(query) if not (ft or esc["escalate"] or pricing["pricing"]) else None
    if ft:
        # free-allowance table / workload arithmetic: code writes the whole answer, with the price pages as sources
        fake = {"pricing": True, "results": [{"provider": p} for p in ft[1]]}
        idx = _add_price_sources(hits, fake)
        text = ft[0] + ("\n\nList-price sources: " + " ".join(f"[{i}]" for i in idx) if idx else "")
        tool_calls = [{"tool": "free_tier", "input": query, "output": [], "unverified": [], "errors": []}]
    elif rc:
        # price question with no volume ("compare Directions price between Ola and Google"): code writes the rate card from pricing.yaml
        idx = _add_price_sources(hits, {"pricing": True, "results": [{"provider": p} for p in rc[1]]})
        text = rc[0] + ("\n\nList-price sources: " + " ".join(f"[{i}]" for i in idx) if idx else "")
        tool_calls = [{"tool": "rate_card", "input": query, "output": [], "unverified": [], "errors": []}]
    elif pricing_intent.is_pure_pricing(query, pricing):
        # Plain "what does it cost / which is cheaper": code writes the whole answer (no model call, no contradictions)
        text = pricing_intent.deterministic_text(pricing, source_idx, esc)
    else:
        limits = facts.relevant_limits(query)
        resp = openai_client().chat.completions.create(
            model=settings.chat_model, temperature=0, max_tokens=900,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": build_user(query, hits, pricing, esc, limits, source_idx, fact_lines)}])
        core = pricing_intent.strip_fake_summary(_clean(resp.choices[0].message.content))
        summary = pricing_intent.render_summary(pricing, source_idx)   # numbers/totals/wording: code, not the model
        if summary:
            core = pricing_intent.strip_prices(core)
        if not core.strip():
            text, no_answer = NO_ANSWER, True
        elif _SELF_REFUSAL.search(core) and not re.search(r"\[\d+\]", core) and not guard.has_strong_domain_term(query):
            # the model itself judged this out of scope: replace its text entirely (it may have added facts from memory)
            text, model_refused = guard.off_topic_message(), True
        else:
            # grounding check: drop cited sentences the cited passages do not support; nothing left -> honest "could not verify"
            allowed = grounding.calc_numbers(tool_calls) | grounding.numbers_in(" ".join(limits + fact_lines))
            g = grounding.verify(core, hits, allowed, query=query)
            ungrounded = g.ungrounded
            removed_claims = g.removed
            removed_why = g.removed_why
            text = f"{g.text}\n\n{summary}" if summary else g.text
            if ungrounded:
                force_cites = [1, 2, 3]

    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(hits)}
                   | {n for n in force_cites if 1 <= n <= len(hits)})
    citations = [{"n": n, "chunk_id": hits[n - 1]["chunk_id"], "doc_url": hits[n - 1]["doc_url"],
                  "provider": hits[n - 1]["provider"], "fetched_at": hits[n - 1]["fetched_at"]} for n in cited]
    blocked = pricing_blocked(pricing) and not ft          # free-tier answers are code-written, not blocked prices
    abstained = blocked or no_answer or model_refused or ungrounded or (looks_abstained(text) and not citations and not ft)
    reason = ("refused:off_topic" if model_refused else "no_answer" if no_answer else "ungrounded" if ungrounded else
              esc["category"] if esc["escalate"] else ("pricing_unverified" if blocked else None))
    return _finish(query, {
        "text": text, "citations": citations, "tool_calls": tool_calls, "abstained": abstained, "refused": model_refused,
        "removed_claims": removed_claims, "removed_why": removed_why,
        "escalate": esc["escalate"] and not model_refused, "reason": reason, "trace_id": trace_id,
        "retrieved": [{"chunk_id": h["chunk_id"], "doc_url": h["doc_url"]} for h in hits]}, esc, persist)
