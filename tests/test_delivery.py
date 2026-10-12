import unittest,sys,pathlib,tempfile,subprocess,json,copy,threading,urllib.request,urllib.error,base64
from reclay.engine import infer,parse,rebuild,validate_model,_model_id
from reclay.server import Handler,ThreadingHTTPServer
EX=pathlib.Path(__file__).resolve().parents[1]/'reclay/examples'
class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result=infer([(EX/name).read_bytes() for name in ['river.wav','riverbend.wav','skyline-observation.wav']]);cls.model=[m for m in cls.result['models'] if m['prefix']['growthSafe']][0];cls.data=(EX/'river.wav').read_bytes()
    def test_cli_refuses_existing_output_and_ambiguous_implicit_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp);schema=p/'schema.json';schema.write_text(json.dumps(self.result));existing=p/'original.bin';existing.write_bytes(b'user original')
            run=lambda args:subprocess.run([sys.executable,'-m','reclay',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
            r=run(['export','--schema',str(schema),'--model',self.model['id'],'--out',str(existing)]);self.assertEqual(r.returncode,2);self.assertEqual(existing.read_bytes(),b'user original')
            r=run(['inspect','--schema',str(schema),str(EX/'river.wav')]);self.assertEqual(r.returncode,2);self.assertIn('ambiguous',r.stderr.lower())
            r=run(['export','--schema',str(schema),'--model',self.model['id'],'--out',str(p/'codec.py')]);self.assertEqual(r.returncode,0,r.stderr)
    def test_model_identity_and_document_tamper_rejected(self):
        bad=copy.deepcopy(self.model);bad['grammar']['alignment']=4
        with self.assertRaises(ValueError):parse(self.data,bad)
        doc=parse(self.data,self.model);doc['chunks'][0]['payloadHex']='00'+doc['chunks'][0]['payloadHex'][2:]
        with self.assertRaises(ValueError):rebuild(doc,self.model)
    def test_rehashed_model_cannot_invent_outside_family_rules(self):
        cases=[]
        bad=copy.deepcopy(self.model);bad['prefix']['totalFields'][0]['bias']=100;cases.append(bad)
        bad=copy.deepcopy(self.model);bad['nesting'][0]['grammar']['endian']='big';cases.append(bad)
        bad=copy.deepcopy(self.model)
        for sample in bad['evidence']['samples']:sample['size']=40096
        cases.append(bad)
        for bad in cases:
            bad['id']=_model_id(bad)
            with self.assertRaises(ValueError):validate_model(bad)
    def test_prefix_search_bound_is_disclosed_and_never_unique(self):
        def chunk(tag,payload):return tag+len(payload).to_bytes(4,'little')+payload
        def sample(n):
            body=chunk(b'ALFA',b'x'*n)+chunk(b'BETA',b'yyy')
            return len(body).to_bytes(4,'little')*16+body
        result=infer([sample(n) for n in [3,5,7]])
        self.assertTrue(result['truncated']);self.assertEqual(result['status'],'ambiguous')
        self.assertIn({'kind':'prefixSelections','limit':32},result['truncationReasons'])
    def test_uppercase_container_rules_cannot_bypass_children(self):
        bad=copy.deepcopy(self.model);bad['nesting'][0]['tagHex']=bad['nesting'][0]['tagHex'].upper();bad['id']=_model_id(bad)
        with self.assertRaises(ValueError):validate_model(bad)
        doc=parse(self.data,self.model);doc['chunks'][1]['tagHex']=doc['chunks'][1]['tagHex'].upper()
        with self.assertRaises(ValueError):rebuild(doc,self.model)
    def test_opaque_header_growth_and_duplicate_edits_refuse(self):
        unsafe=[m for m in self.result['models'] if not m['prefix']['growthSafe']][0];doc=parse(self.data,unsafe)
        leaves=[]
        def collect(nodes,p=[]):
            for i,n in enumerate(nodes):
                if n.get('children'):collect(n['children'],p+[i])
                else:leaves.append((p+[i],n))
        collect(doc['chunks']);path,n=leaves[0]
        with self.assertRaises(ValueError):rebuild(doc,unsafe,[{'path':path,'payloadHex':n['payloadHex']+'0000'}])
        doc=parse(self.data,self.model);edit={'path':[0],'payloadHex':doc['chunks'][0]['payloadHex']}
        with self.assertRaises(ValueError):rebuild(doc,self.model,[edit,edit])
    def test_server_rejects_cross_origin_paths_and_exec_inputs(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            request=urllib.request.Request(base+'/api/parse',data=json.dumps({'data':base64.b64encode(self.data).decode(),'model':self.model}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=10) as response:self.assertEqual(json.loads(response.read())['modelId'],self.model['id'])
            for path,origin in [('/api/parse','https://example.org'),('/api/run',None)]:
                headers={'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                request=urllib.request.Request(base+path,data=json.dumps({'data':base64.b64encode(self.data).decode(),'model':self.model,'command':'uname','path':'/private/file'}).encode(),headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request,timeout=10)
                self.assertIn(caught.exception.code,[400,404]);caught.exception.close()
            request=urllib.request.Request(base+'/api/export',data=b'{}',headers={'Content-Type':'text/plain'})
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request,timeout=10)
            caught.exception.close()
        finally:server.shutdown();server.server_close();t.join(timeout=2)
if __name__=='__main__':unittest.main()
