"""5-phase roadmap (two engineers). Regenerate: python roadmap.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

rows = [  # (label, first_week, last_week, color, hatch, text) - a bar covers whole weeks first..last
    ("Phase 1  One backtest process", 1, 5, "#f59e0b", None, "≈34 d · one pipeline, one kernel, one registry"),
    ("   Eng A: schema · data · costs · dispatcher · eval · API/UI", 1, 5, "#fcd34d", None, ""),
    ("   Eng B: hbt portfolio mode · port strategies · parity", 1, 5, "#fcd34d", None, ""),
    ("Phase 2  Idea intake + trade-data contracts", 6, 8, "#8b5cf6", None, "≈12 d (Eng A) · IdeaSpec · gates · outbox"),
    ("Phase 3  Paper trading on one order path", 6, 10, "#10b981", None, "≈20 d (Eng B) · Execution Gateway · portfolio risk"),
    ("   Paper period (calendar: ≥20 sessions, ≥30 round trips / idea)", 11, 15, "#a7f3d0", "//", ""),
    ("Phase 4  Real-time + live readiness", 9, 14, "#3b82f6", None, "≈18 d (A + B) · WS bars · step_bar · VPS · approval"),
    ("   Owner: tariffs, static IP, broker algo rules, 10 Analyzer sessions", 10, 15, "#bfdbfe", "//", ""),
    ("Phase 5  Live pilot → scale", 16, 20, "#ef4444", None, "≈10 d + ≥20 pilot sessions"),
    ("   Live pilot (calendar: dry run + ≥20 sessions at minimum caps)", 16, 20, "#fecaca", "//", ""),
]
miles = [(5, "M1 one backtest\nprocess", 0), (8, "M2 ideas in,\nevents out", 1), (10, "M3 first ideas\non paper", 0),
         (15, "M4 readiness\nsign-off", 1), (20, "M5 pilot review\n→ scale", 0)]

fig, ax = plt.subplots(figsize=(18, 7.8), dpi=110)
for i, (lab, s, e, c, h, txt) in enumerate(rows):
    y = len(rows) - i
    ax.barh(y, e - s + 1, left=s - 1, height=0.62, color=c, edgecolor="#333333" if not lab.startswith("   ") else "#777777",
            hatch=h, linewidth=1.1 if not lab.startswith("   ") else 0.6)
    if txt:
        ax.text(e + 0.15, y, txt, va="center", fontsize=10, color="#222222")
ax.set_yticks([len(rows) - i for i in range(len(rows))])
ax.set_yticklabels([r[0] for r in rows], fontsize=11)
for lbl in ax.get_yticklabels():
    if not lbl.get_text().startswith("   "):
        lbl.set_fontweight("bold")
for w, t, lvl in miles:
    ax.axvline(w, color="#444444", linestyle=":", linewidth=1)
    ax.text(w, len(rows) + 0.7 + 0.95 * lvl, "◆ " + t, ha="center", va="bottom", fontsize=9.5, color="#111111")
ax.set_xlim(0, 21.5)
ax.set_ylim(0.3, len(rows) + 3.1)
ax.set_xticks([w - 0.5 for w in range(1, 22)])
ax.set_xticklabels([f"W{w}" for w in range(1, 22)], fontsize=9)
ax.set_xticks(range(0, 22), minor=True)
ax.grid(axis="x", which="minor", color="#eeeeee")
ax.set_axisbelow(True)
for sp in ("top", "right"):
    ax.spines[sp].set_visible(False)
ax.set_title("Trading engine - 5-phase roadmap (two engineers; hatched = calendar time that cannot be compressed)",
             fontsize=14, pad=12)
ax.legend(handles=[Patch(facecolor="#dddddd", edgecolor="#333", label="engineering"),
                   Patch(facecolor="#ffffff", edgecolor="#333", hatch="//", label="calendar / owner")],
          loc="lower right", fontsize=10, frameon=False)
fig.tight_layout()
fig.savefig("roadmap_5_phases.png")
