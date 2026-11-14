"""Render both LangGraph graphs from code into docs/images/*.mmd (Mermaid) and, if online, PNG."""
from pathlib import Path

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph

out = Path(__file__).resolve().parents[1] / "docs" / "images"
out.mkdir(parents=True, exist_ok=True)
for name, g in (("bid_graph", build_bid_graph()), ("conversation_graph", build_conversation_graph())):
    mmd = g.get_graph().draw_mermaid()
    (out / f"{name}.mmd").write_text(mmd)
    try:
        (out / f"{name}.png").write_bytes(g.get_graph().draw_mermaid_png())
    except Exception as e:  # noqa: BLE001 - PNG rendering needs network access to mermaid.ink
        print(f"{name}: PNG skipped ({type(e).__name__})")
    print("wrote", out / f"{name}.mmd")
