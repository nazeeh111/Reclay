"""Build a standard pure-Python wheel without a build dependency."""
import base64,hashlib,io,csv,pathlib,zipfile
root=pathlib.Path(__file__).resolve().parents[1];out=root/'dist';out.mkdir(exist_ok=True)
name='reclay-0.1.0-py3-none-any.whl'; meta='reclay-0.1.0.dist-info'; records=[]
with zipfile.ZipFile(out/name,'w',zipfile.ZIP_DEFLATED) as z:
 def put(path,data):
  info=zipfile.ZipInfo(path,(2026,10,12,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16;z.writestr(info,data);records.append((path,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='),str(len(data))))
 for f in sorted((root/'reclay').rglob('*')):
  if f.is_file() and '__pycache__' not in f.parts and not f.name.endswith('.pyc'):put(f.relative_to(root).as_posix(),f.read_bytes())
 put(meta+'/METADATA',b'Metadata-Version: 2.4\nName: reclay\nVersion: 0.1.0\nSummary: Infer binary chunk rules and compile standalone editors\nRequires-Python: >=3.10\nLicense-Expression: MIT\nLicense-File: licenses/LICENSE\n')
 put(meta+'/WHEEL',b'Wheel-Version: 1.0\nGenerator: Reclay stdlib packager\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
 put(meta+'/entry_points.txt',b'[console_scripts]\nreclay = reclay.cli:main\n')
 put(meta+'/licenses/LICENSE',(root/'LICENSE').read_bytes())
 records.append((meta+'/RECORD','',''));text=io.StringIO();csv.writer(text,lineterminator='\n').writerows(records);z.writestr(meta+'/RECORD',text.getvalue().encode())
print(out/name)
