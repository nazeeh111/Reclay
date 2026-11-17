"""Independent byte-writer and holdout, budget, identity, CLI and RPC controls."""
import base64, copy, hashlib, http.client, json, struct, subprocess, sys, tempfile, threading, unittest
from http.server import ThreadingHTTPServer
from reclay.server import Handler
from pathlib import Path
from reclay.engine import infer, parse, rebuild, validate_hypotheses, ensemble_sha256, ReclayError
from reclay.browser_dispatch import dispatch


def writer(n, alignment=4, other=None):
    pieces=[]
    for tag,payload in ((b'HEAD',b'H'*n),(b'DATA',b'D'*(n+4 if other is None else other))):
        pieces.append(tag+struct.pack('<I',len(payload))+payload+b'\0'*((-len(payload))%alignment))
    return b''.join(pieces)

class Holdout(unittest.TestCase):
    def test_saved_record_reopens_without_inference_or_lost_identities(self):
        # Value: protects=save/reopen/continue; seam=none; synthetic byte family only.
        from reclay import engine
        saved = self.admit([writer(12)])
        opened = engine.open_schema(json.loads(json.dumps(saved)))
        self.assertEqual(opened["ensemble"], saved)
        self.assertEqual(opened["sha256"], ensemble_sha256(saved))
        continued = validate_hypotheses(opened["ensemble"], [writer(5)], expected_sha256=opened["sha256"])
        self.assertEqual(len(continued["models"]), 1)
        self.assertEqual(len(continued["holdoutEvidence"]), 2)
        self.assertEqual(continued["evidence"], self.ensemble["evidence"])
        model = saved["models"][0]
        selected = engine.open_schema(model)
        self.assertEqual(selected["ensemble"]["models"], [model])
        self.assertTrue(selected["ensemble"]["truncated"])
        self.assertNotEqual(selected["ensemble"]["status"], "unique")
        self.assertEqual(dispatch("/api/open-schema", {"record": saved}), opened)
        rejected = self.admit([writer(5)[:-3]])
        self.assertEqual(engine.open_schema(rejected)["ensemble"]["models"], [])
        for edit in (
            {"models": [dict(model, id="0" * 64)]},
            {"truncated": "false"},
            {"truncationReasons": {}},
            {"evidence": []},
            {"validation": []},
            {"models": [model, model]},
        ):
            bad = dict(saved, **edit)
            with self.subTest(edit=edit):
                with self.assertRaises(ReclayError):
                    engine.open_schema(bad)

    @classmethod
    def setUpClass(cls):
        cls.training=[writer(4),writer(8)]
        cls.ensemble=infer(cls.training)
        cls.digest=ensemble_sha256(cls.ensemble)
        assert len(cls.ensemble['models'])==3 and not cls.ensemble['truncated']

    def admit(self,data,ensemble=None):
        source=ensemble or self.ensemble
        return validate_hypotheses(source,data,expected_sha256=ensemble_sha256(source))

    def test_independent_holdout_narrows_and_preserves_exact_rebuild_edit(self):
        held=writer(5);result=self.admit([held]);self.assertEqual(len(result['models']),1)
        self.assertEqual(result['models'][0]['grammar']['alignment'],4)
        self.assertEqual([x['status'] for x in result['validation']['outcomes']].count('refused'),2)
        for model in result['models']:
            doc=parse(held,model);self.assertEqual(rebuild(doc,model),held)
            self.assertEqual(rebuild(doc,model,[{'path':[0],'payloadHex':(b'H'*9).hex()}]),writer(9,other=9))
        self.assertEqual(result['status'],'consistent');self.assertFalse(result['truncated'])

    def test_ambiguous_truncated_inconsistent_and_refusals(self):
        result=self.admit([writer(12)]);self.assertEqual(len(result['models']),3)
        truncated=copy.deepcopy(self.ensemble);truncated['truncated']=True
        result=self.admit([writer(5)],truncated);self.assertTrue(result['truncated']);self.assertNotEqual(result['status'],'unique')
        bad=writer(5)[:-3];result=self.admit([bad]);self.assertEqual(result['models'],[])
        self.assertTrue(all(x['status']=='refused' for x in result['validation']['outcomes']))
        for blobs,code in (([self.training[0]],'reused-sample'),([writer(5),writer(5)],'reused-sample')):
            with self.assertRaises(ReclayError) as caught:self.admit(blobs)
            self.assertEqual(caught.exception.code,code)
        with self.assertRaises(ReclayError) as caught:validate_hypotheses(self.ensemble,[writer(5)],expected_sha256='0'*64)
        self.assertEqual(caught.exception.code,'stale-ensemble')
        badmodel=copy.deepcopy(self.ensemble);badmodel['models'][0]['start']=63
        with self.assertRaises(ReclayError) as caught:self.admit([writer(5)],badmodel)
        self.assertEqual(caught.exception.code,'invalid-model')
        with self.assertRaises(ReclayError) as caught:self.admit([b'X'*(1024*1024)]*5)
        self.assertIn(caught.exception.code,('holdout-work-bound','input-bound'))
        first=self.admit([writer(12)])
        with self.assertRaises(ReclayError) as caught:self.admit([writer(12)],first)
        self.assertEqual(caught.exception.code,'reused-sample')

    def test_model_sample_work_budget_refuses_before_evaluation(self):
        ensemble=copy.deepcopy(self.ensemble);models=[]
        for i in range(33):
            model=copy.deepcopy(ensemble['models'][0]);model['evidence']['hypothesisFamily']='synthetic-'+str(i)
            value={k:v for k,v in model.items() if k!='id'}
            model['id']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
            models.append(model)
        ensemble['models']=models
        with self.assertRaises(ReclayError) as caught:self.admit([writer(i) for i in range(17,25)],ensemble)
        self.assertEqual(caught.exception.code,'holdout-work-bound')

    def test_admitted_model_keeps_length_overflow_refusal(self):
        def write_short(n):
            return b''.join(tag+struct.pack('<H',len(data))+data for tag,data in ((b'HH',b'H'*n),(b'DD',b'D'*(n+2))))
        source=infer([write_short(4),write_short(6)])
        result=validate_hypotheses(source,[write_short(5)],expected_sha256=ensemble_sha256(source))
        self.assertTrue(result['models'])
        for model in result['models']:
            doc=parse(write_short(5),model)
            with self.assertRaises(ReclayError) as caught:
                rebuild(doc,model,[{'path':[0],'payloadHex':(b'X'*65536).hex()}])
            self.assertEqual(caught.exception.code,'length-overflow')

    def test_browser_dispatch_matches_owner(self):
        payload={'ensemble':self.ensemble,'expectedSha256':self.digest,'samples':[{'data':base64.b64encode(writer(5)).decode()}]}
        self.assertEqual(dispatch('/api/ensemble-identity',{'ensemble':self.ensemble}),{'sha256':self.digest})
        self.assertEqual(dispatch('/api/validate-hypotheses',payload),self.admit([writer(5)]))

    def test_actual_loopback_api_and_stale_document_refusal(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            payload={'ensemble':self.ensemble,'expectedSha256':self.digest,'samples':[{'data':base64.b64encode(writer(5)).decode()}]}
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/api/open-schema',json.dumps({'record':self.ensemble}),{'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,200);opened=json.loads(response.read());self.assertEqual(opened['ensemble'],self.ensemble);self.assertEqual(opened['sha256'],self.digest)
            conn.request('POST','/api/open-schema',json.dumps({'record':{'models':[]}}),{'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,400);response.read()
            conn.request('POST','/api/ensemble-identity',json.dumps({'ensemble':self.ensemble}),{'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['sha256'],self.digest)
            conn.request('POST','/api/validate-hypotheses',json.dumps(payload),{'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,200);result=json.loads(response.read());conn.close()
            self.assertEqual(result,self.admit([writer(5)]))
            model=result['models'][0];doc=parse(writer(5),model);doc['evidence']['sha256']='0'*64
            with self.assertRaises(ReclayError) as caught:rebuild(doc,model)
            self.assertEqual(caught.exception.code,'document-tampered')
        finally:
            server.shutdown();thread.join(timeout=5);server.server_close()

    def test_cli_narrow_inspect_edit_export_and_stale_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);schema=root/'input.json';schema.write_text(json.dumps(self.ensemble));held=root/'hold.bin';held.write_bytes(writer(5));out=root/'validated.json'
            base=[sys.executable,'-m','reclay'];args=['validate-hypotheses','--schema',str(schema),'--expect-schema-sha256',self.digest,'--out',str(out),str(held)]
            p=subprocess.run(base+args,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            obj=json.loads(out.read_text());mid=obj['models'][0]['id']
            p=subprocess.run(base+['inspect','--schema',str(out),str(held)],capture_output=True,text=True);self.assertNotEqual(p.returncode,0)
            p=subprocess.run(base+['inspect','--schema',str(out),'--model',mid,str(held)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            edited=root/'edited.bin';p=subprocess.run(base+['edit','--schema',str(out),'--model',mid,str(held),'--path','0','--hex',(b'H'*9).hex(),'--out',str(edited)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(edited.read_bytes(),writer(9,other=9))
            codec=root/'codec.py';p=subprocess.run(base+['export','--schema',str(out),'--model',mid,'--out',str(codec)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            p=subprocess.run([sys.executable,str(codec),'inspect',str(held)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            old=out.read_bytes();p=subprocess.run(base+args,capture_output=True,text=True);self.assertNotEqual(p.returncode,0);self.assertEqual(old,out.read_bytes())
            args[4]='0'*64;args[6]=str(root/'stale.json');p=subprocess.run(base+args,capture_output=True,text=True);self.assertNotEqual(p.returncode,0);self.assertFalse((root/'stale.json').exists())
