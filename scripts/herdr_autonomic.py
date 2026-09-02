#!/usr/bin/env python3
"""Schema-discovered, receipted Groq autonomic mode for Herdr."""
from __future__ import annotations
import argparse, dataclasses, datetime as dt, hashlib, json, os, pathlib, shutil, subprocess, sys, time, urllib.error, urllib.request, uuid
from typing import Any
ROOT=pathlib.Path(__file__).resolve().parents[1]
SCHEMA=ROOT/"docs/next/api/herdr-api.schema.json"; MODELS=ROOT/"autonomic/models.generated.json"; RISK=ROOT/"autonomic/risk.generated.json"
GROQ_URL="https://api.groq.com/openai/v1/chat/completions"
class AutonomicError(RuntimeError): pass
class Refused(AutonomicError):
    def __init__(self,code,message): super().__init__(message); self.code=code
@dataclasses.dataclass(frozen=True)
class ModelSpec:
    id:str; input_per_million:float; output_per_million:float; public_price:bool; production:bool; json_mode:bool; notes:str=""
    def cost(self,i,o): return (i*self.input_per_million+o*self.output_per_million)/1_000_000
@dataclasses.dataclass
class Policy:
    models:dict[str,ModelSpec]; exact_risk:dict[str,str]; observe_suffixes:tuple[str,...]
    @classmethod
    def load(cls,models_path,risk_path):
        m=json.loads(pathlib.Path(models_path).read_text()); r=json.loads(pathlib.Path(risk_path).read_text())
        models={x["id"]:ModelSpec(x["id"],float(x["input_per_million"]),float(x["output_per_million"]),str(x["public_price"]).lower()=="true",str(x["production"]).lower()=="true",str(x["json_mode"]).lower()=="true",x.get("notes","")) for x in m["models"]}
        return cls(models,{x["method"]:x["risk"] for x in r["exact"]},tuple(r["observe_suffixes"]))
    def cheapest_model(self,requested=None):
        if requested:
            if requested not in self.models: raise Refused("REFUSED_UNKNOWN_MODEL",requested)
            m=self.models[requested]
            if not(m.production and m.json_mode): raise Refused("REFUSED_MODEL_CAPABILITY",requested)
            return m
        eligible=[m for m in self.models.values() if m.public_price and m.production and m.json_mode]
        if not eligible: raise Refused("REFUSED_NO_PRICED_MODEL","no eligible public-priced model")
        return min(eligible,key=lambda m:m.cost(4000,1000))
    def classify(self,method):
        if method in self.exact_risk:return self.exact_risk[method]
        return "observe" if any(method.endswith(s) for s in self.observe_suffixes) else "bounded"
def state_path():
    root=pathlib.Path(os.environ.get("HERDR_AUTONOMIC_STATE_DIR",os.environ.get("XDG_STATE_HOME",pathlib.Path.home()/".local/state")))
    return root/"herdr/autonomic/receipts.jsonl"
class ReceiptLedger:
    def __init__(self,path): self.path=pathlib.Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    @staticmethod
    def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    def last(self):
        if not self.path.exists(): return None
        rows=[json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]; return rows[-1].get("receipt_digest") if rows else None
    def append(self,x):
        x=dict(x); x["previous_receipt"]=self.last(); x["receipt_algorithm"]="sha256"; x["receipt_digest"]=self.digest(x)
        with self.path.open("a",encoding="utf-8") as f:f.write(json.dumps(x,sort_keys=True)+"\n")
        return x
    def verify(self):
        if not self.path.exists(): return False,"receipt ledger does not exist"
        prev=None; n=0
        for n,line in enumerate(self.path.read_text().splitlines(),1):
            x=json.loads(line); expected=x.pop("receipt_digest",None)
            if x.get("previous_receipt")!=prev:return False,f"receipt {n}: predecessor mismatch"
            if self.digest(x)!=expected:return False,f"receipt {n}: digest mismatch"
            prev=expected
        return True,f"verified {n} receipt(s)"
def run_herdr(args,timeout=30):
    p=subprocess.run([os.environ.get("HERDR_BIN","herdr"),*args],capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise AutonomicError(p.stderr.strip() or f"herdr exited {p.returncode}")
    try:return json.loads(p.stdout)
    except json.JSONDecodeError as e:raise AutonomicError("herdr returned non-JSON") from e
def schema_methods(value:Any):
    out=set()
    def walk(v):
        if isinstance(v,dict):
            p=v.get("properties",{}); m=p.get("method",{}) if isinstance(p,dict) else {}
            if isinstance(m,dict) and isinstance(m.get("const"),str):out.add(m["const"])
            for c in v.values():walk(c)
        elif isinstance(v,list):
            for c in v:walk(c)
    walk(value); return out
def discover_methods(path):
    try:schema=run_herdr(["api","schema","--json"])
    except (AutonomicError,OSError):schema=json.loads(pathlib.Path(path).read_text())
    methods=sorted(schema_methods(schema))
    if not methods:raise AutonomicError("Herdr schema exposed no methods")
    return methods
def admitted_methods(policy,methods,allow_destructive,allow_unbounded):
    out=[]
    for m in methods:
        risk=policy.classify(m)
        if risk=="destructive" and not allow_destructive:continue
        if risk=="unbounded" and not allow_unbounded:continue
        out.append({"method":m,"risk":risk})
    return out
def admit(intent,policy,methods,allow_destructive,allow_unbounded):
    if not isinstance(intent,dict) or not isinstance(intent.get("method"),str) or not isinstance(intent.get("params",{}),dict):raise Refused("REFUSED_INTENT_SHAPE","invalid intent")
    m=intent["method"]
    if m not in methods:raise Refused("REFUSED_UNKNOWN_ACTION",m)
    risk=policy.classify(m)
    if risk=="destructive" and not allow_destructive:raise Refused("REFUSED_DESTRUCTIVE",m)
    if risk=="unbounded" and not allow_unbounded:raise Refused("REFUSED_UNBOUNDED",m)
    return risk
def observe():return run_herdr(["api","snapshot"])
def actuate(intent):return run_herdr(["api","request","--json",json.dumps({"id":f"autonomic:{uuid.uuid4()}","method":intent["method"],"params":intent.get("params",{})},separators=(",",":"))])
def h(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def groq_plan(key,model,goal,obs,actions,limit,timeout):
    text=json.dumps(obs,sort_keys=True,ensure_ascii=False); text=text if len(text)<=limit else text[:limit//2]+"...TRUNCATED..."+text[-limit//2:]
    system="You are Herdr SELECT/CONSTRUCT only; you have zero DO authority. Return JSON: goal_satisfied:boolean,rationale:string,intent:object|null,postcondition:string. Choose at most one exact method from admitted_actions. Never invent methods. Prefer observe/reversible actions."
    body={"model":model.id,"temperature":0,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":system},{"role":"user","content":json.dumps({"goal":goal,"observation_json":text,"admitted_actions":actions},separators=(",",":"))}]}
    req=urllib.request.Request(GROQ_URL,data=json.dumps(body).encode(),headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:raw=json.loads(r.read())
    except urllib.error.HTTPError as e:raise AutonomicError(f"Groq HTTP {e.code}: {e.read().decode(errors='replace')[:1000]}") from e
    except urllib.error.URLError as e:raise AutonomicError(f"Groq transport: {e}") from e
    try:plan=json.loads(raw["choices"][0]["message"]["content"])
    except Exception as e:raise AutonomicError("Groq returned invalid planner JSON") from e
    u=raw.get("usage") or {}; return plan,{"provider":"groq","model":model.id,"system_fingerprint":raw.get("system_fingerprint"),"usage":u},model.cost(int(u.get("prompt_tokens") or 0),int(u.get("completion_tokens") or 0))
def run_cycle(a,p,ledger,methods):
    key=os.environ.get("GROQ_API_KEY")
    if not key:raise Refused("REFUSED_MISSING_GROQ_KEY","GROQ_API_KEY is required")
    model=p.cheapest_model(a.model); actions=admitted_methods(p,methods,a.allow_destructive,a.allow_unbounded); rid=str(uuid.uuid4()); total=0
    for step in range(1,a.max_steps+1):
        before=observe(); plan,evidence,cost=groq_plan(key,model,a.goal,before,actions,a.max_observation_chars,a.timeout); total+=cost
        r={"schema":"herdr-autonomic-receipt-v1","run_id":rid,"step":step,"timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),"goal":a.goal,"model":model.id,"observation_digest":h(before),"planner":evidence,"estimated_total_cost_usd":total,"plan":plan}
        if total>a.max_cost_usd:r.update(standing="REFUSED",refusal="REFUSED_COST_BUDGET");ledger.append(r);raise Refused("REFUSED_COST_BUDGET",str(total))
        if plan.get("goal_satisfied") is True:r.update(standing="ALIVE",outcome="goal_satisfied",actuated=False);ledger.append(r);print(json.dumps({"standing":"ALIVE","run_id":rid,"steps":step,"cost":total}));return 0
        intent=plan.get("intent")
        try:risk=admit(intent,p,methods,a.allow_destructive,a.allow_unbounded)
        except Refused as e:r.update(standing="REFUSED",refusal=e.code,actuated=False);ledger.append(r);raise
        r["admission"]={"method":intent["method"],"risk":risk}
        if a.dry_run:r.update(standing="PARTIAL_ALIVE",outcome="admitted_dry_run",actuated=False);ledger.append(r);print(json.dumps({"standing":"PARTIAL_ALIVE","intent":intent,"cost":total}));return 0
        result=actuate(intent); after=observe(); r.update(standing="PARTIAL_ALIVE",outcome="actuated_and_reobserved",actuated=True,actuation_result=result,post_observation_digest=h(after),observed_change=h(before)!=h(after));ledger.append(r)
        if a.once:return 0
        if a.cycle_delay:time.sleep(a.cycle_delay)
    ledger.append({"schema":"herdr-autonomic-receipt-v1","run_id":rid,"step":a.max_steps,"standing":"BLOCKED","outcome":"cycle_budget_exhausted"});return 4
def build_parser():
    p=argparse.ArgumentParser();p.add_argument("--schema",type=pathlib.Path,default=SCHEMA);p.add_argument("--models",type=pathlib.Path,default=MODELS);p.add_argument("--risk",type=pathlib.Path,default=RISK);p.add_argument("--receipts",type=pathlib.Path,default=state_path());s=p.add_subparsers(dest="command",required=True)
    r=s.add_parser("run");r.add_argument("goal");r.add_argument("--model");r.add_argument("--max-steps",type=int,default=12);r.add_argument("--max-cost-usd",type=float,default=.25);r.add_argument("--max-observation-chars",type=int,default=60000);r.add_argument("--timeout",type=float,default=30);r.add_argument("--cycle-delay",type=float,default=.25);r.add_argument("--dry-run",action="store_true");r.add_argument("--once",action="store_true");r.add_argument("--allow-destructive",action="store_true");r.add_argument("--allow-unbounded",action="store_true")
    d=s.add_parser("doctor");d.add_argument("--offline",action="store_true");s.add_parser("capabilities");s.add_parser("replay");return p
def main(argv=None):
    a=build_parser().parse_args(argv)
    try:
        p=Policy.load(a.models,a.risk);ledger=ReceiptLedger(a.receipts);methods=discover_methods(a.schema)
        if a.command=="capabilities":print(json.dumps({"schema_methods":len(methods),"actions":admitted_methods(p,methods,True,True),"cheapest_public_model":p.cheapest_model().id},indent=2));return 0
        if a.command=="replay":ok,msg=ledger.verify();print(json.dumps({"standing":"ALIVE" if ok else "REFUSED","message":msg}));return 0 if ok else 3
        if a.command=="doctor":
            checks={"herdr_binary":shutil.which(os.environ.get("HERDR_BIN","herdr")) is not None,"schema_methods":len(methods),"cheapest_public_model":p.cheapest_model().id,"groq_key":"SKIPPED_OFFLINE" if a.offline else bool(os.environ.get("GROQ_API_KEY"))};print(json.dumps({"standing":"PARTIAL_ALIVE" if checks["herdr_binary"] else "BLOCKED","checks":checks},indent=2));return 0 if checks["herdr_binary"] else 2
        if a.max_steps<1:raise Refused("REFUSED_CYCLE_BUDGET","max steps")
        if a.max_cost_usd<=0:raise Refused("REFUSED_COST_BUDGET","max cost")
        return run_cycle(a,p,ledger,methods)
    except Refused as e:print(json.dumps({"standing":"REFUSED","code":e.code,"message":str(e)}),file=sys.stderr);return 3
    except (AutonomicError,OSError,json.JSONDecodeError) as e:print(json.dumps({"standing":"BLOCKED","message":str(e)}),file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
