"""
Rule-based event kinds for exchange filings (first-order labelling; versioned so items can be re-labelled later).

Order matters: the first matching rule wins, so specific rules (auditor resignation) come before generic ones
(management change). The classifier reads the source category + title + the first part of the body.
"""
from __future__ import annotations

import re

VERSION = "rules-1"

_RULES: tuple[tuple[str, str], ...] = (
    ("results", r"financial results?|results? for the (quarter|period|half|year)|(un)?audited (standalone|consolidated|financial)"),
    ("board_outcome", r"outcome of (the )?board meeting|board meeting outcome"),
    ("board_meeting_intimation", r"(intimation|notice) of (the )?board meeting|board meeting intimation|prior intimation"),
    ("auditor_change", r"auditor.{0,40}(resign|cessation|change|appoint)|(resign|cessation).{0,40}auditor"),
    ("pledge_encumbrance", r"pledge|encumbrance|regulation 31\b|reg\.? ?31\b|non[- ]disposal|invocation"),
    ("sast_insider", r"regulation 29|reg\.? ?29\b|substantial acquisition|takeover regulations|sast\b|insider trading|"
                     r"regulation 7 ?\(2\)|prohibition of insider"),
    ("credit_rating", r"credit rating|rating (action|rationale|reaffirm|upgrade|downgrade|assigned|revised)|"
                      r"\b(crisil|icra|care ratings|india ratings|acuit[eé]|brickwork)\b"),
    ("order_win", r"(award|receipt|receiv|bag|secur|win|won|obtain)\w* (of )?(an? |the )?(new |large |major |significant )?"
                  r"(orders?|contracts?|work orders?|loa\b|letter of (award|acceptance|intent))|letter of (award|acceptance)|"
                  r"order (inflow|book|win)|bagged"),
    ("buyback", r"buy ?-?back"),
    ("fund_raising", r"\bqip\b|qualified institutions? placement|preferential (issue|allotment)|rights issue|"
                     r"\bwarrants?\b|fund ?raising|raising of funds|non[- ]convertible debentures|\bncds?\b|"
                     r"commercial paper"),
    ("dividend", r"\bdividend"),
    ("bonus_split", r"\bbonus\b|sub-?division|stock split|split of (equity )?shares"),
    ("m_and_a", r"acquisition|amalgamation|merger|scheme of arrangement|demerger|open offer|divest|joint venture|"
                r"strategic (investment|partnership)|stake sale"),
    ("management_change", r"resignation|appointment|cessation|re-?appoint|change in (key managerial|directors?|management)|"
                          r"\b(md|ceo|cfo|coo|whole[- ]time director|company secretary)\b|chief (executive|financial)"),
    ("litigation_regulatory", r"litigation|penalt|show cause|adjudication|\border (passed|received)|tax demand|"
                              r"gst (demand|notice)|income tax|search|court|tribunal|\bnclt\b|\bsebi order|"
                              r"regulatory action|warning letter|\busfda\b|form 483|import alert"),
    ("transcript", r"transcript"),
    ("audio_recording", r"audio (recording|clip)|recording of (the )?(earnings|conference|analyst)"),
    ("investor_meet", r"analysts?( / |/| and )?(institutional )?investors? meet|investor (meet|conference)|"
                      r"con(ference)? ?call|earnings call|analyst meet"),
    ("investor_presentation", r"investor presentation|presentation"),
    ("press_release", r"press release|media release"),
    ("business_update", r"business update|sales (update|volume|number)|production (update|number)|"
                        r"monthly (sales|update)|operational update"),
    ("agm_egm", r"\bagm\b|\begm\b|annual general meeting|extra-?ordinary general meeting|postal ballot|e-?voting|"
                r"book closure|record date"),
    ("trading_window", r"trading window"),
    ("shareholding", r"shareholding pattern"),
    ("annual_report", r"annual report|business responsibility|brsr"),
    ("compliance", r"regulation (74|76|40|23|24a|57|52|54|39)|compliance certificate|reconciliation of share capital|"
                   r"statement of (deviation|investor complaints)|secretarial compliance|loss of share certificate|"
                   r"duplicate share"),
    ("clarification", r"clarification|spurt in (price|volume)|news verification|price movement"),
)
_COMPILED = tuple((kind, re.compile(rx, re.I)) for kind, rx in _RULES)

# Kinds that usually matter for signals (attachment downloads, alerts).
IMPORTANT_KINDS = frozenset({"results", "board_outcome", "auditor_change", "pledge_encumbrance", "sast_insider",
                             "credit_rating", "order_win", "buyback", "fund_raising", "m_and_a", "management_change",
                             "litigation_regulatory", "transcript", "investor_presentation", "business_update",
                             "clarification"})


def classify(category: str | None, title: str | None, body: str | None = None) -> str:
    text = " ".join(x for x in (category, title, (body or "")[:600]) if x)
    if not text:
        return "other"
    head = " ".join(x for x in (category, title) if x)
    for kind, rx in _COMPILED:                 # subject/category first: it is the filer's own label
        if head and rx.search(head):
            return kind
    for kind, rx in _COMPILED:
        if rx.search(text):
            return kind
    return "other"
