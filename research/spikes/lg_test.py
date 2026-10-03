import sys, sqlite3
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
class S(TypedDict, total=False):
    filled: list; approved: str
def fill(s): print("  node fill (runs once)"); return {"filled": ["name","email","resume"]}
def gate(s):
    ans = interrupt({"review": s["filled"], "approve_url": "http://localhost:8787/approve/run1"})
    return {"approved": ans}
def submit(s): print("  node submit ->", s["approved"]); return {}
g = StateGraph(S); g.add_node("fill", fill); g.add_node("gate", gate); g.add_node("submit", submit)
g.add_edge(START,"fill"); g.add_edge("fill","gate"); g.add_edge("gate","submit"); g.add_edge("submit",END)
conn = sqlite3.connect("lg.db", check_same_thread=False); app = g.compile(checkpointer=SqliteSaver(conn))
cfg = {"configurable":{"thread_id":"run1"}}
if sys.argv[1]=="a":
    r = app.invoke({}, cfg); print("A paused:", r["__interrupt__"][0].value)
else:
    print("B state next:", app.get_state(cfg).next)
    app.invoke(Command(resume="APPROVED"), cfg); print("B done")
