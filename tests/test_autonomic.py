import importlib.util, json, pathlib, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("herdr_autonomic",ROOT/"scripts/herdr_autonomic.py"); module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
class Tests(unittest.TestCase):
    def setUp(self): self.p=module.Policy.load(ROOT/"autonomic/models.generated.json",ROOT/"autonomic/risk.generated.json"); self.methods={"agent.prompt","worktree.remove","command.invoke","workspace.list"}
    def test_schema_discovery(self):
        schema={"oneOf":[{"properties":{"method":{"const":"agent.prompt"}}},{"properties":{"method":{"const":"plugin.action.invoke"}}}]}; self.assertEqual(module.schema_methods(schema),{"agent.prompt","plugin.action.invoke"})
    def test_cheapest_public_model(self): self.assertEqual(self.p.cheapest_model().id,"openai/gpt-oss-20b")
    def test_destructive_and_unbounded_refuse_without_grants(self):
        with self.assertRaises(module.Refused): module.admit({"method":"worktree.remove","params":{}},self.p,self.methods,False,False)
        with self.assertRaises(module.Refused): module.admit({"method":"command.invoke","params":{}},self.p,self.methods,True,False)
    def test_unknown_method_refused(self):
        with self.assertRaises(module.Refused): module.admit({"method":"shell.exec","params":{}},self.p,self.methods,True,True)
    def test_receipt_tamper_falsifier(self):
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/"r.jsonl"; ledger=module.ReceiptLedger(path); a=ledger.append({"step":1}); b=ledger.append({"step":2}); self.assertEqual(b["previous_receipt"],a["receipt_digest"]); self.assertTrue(ledger.verify()[0]); rows=[json.loads(x) for x in path.read_text().splitlines()]; rows[0]["step"]=99; path.write_text("\n".join(json.dumps(x) for x in rows)+"\n"); self.assertFalse(ledger.verify()[0])
if __name__=="__main__": unittest.main()
