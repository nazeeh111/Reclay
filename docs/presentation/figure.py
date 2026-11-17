"""Encode the executed byte ranges and integer rewrites, without invented structure.
Container meanings (title/private metadata/pixels) come from the independent reader;
the learner receives bytes and supplied spans, not TIFF field semantics.
"""
from pathlib import Path
import json,html
P=Path(__file__).resolve().parent;d=json.loads((P/'journey.json').read_text());r=d['receipt'];a=(P/'working.tif').read_bytes();b=(P/'edited.tif').read_bytes();assert len(a)==1008 and len(b)==976;assert a[240:]==b[208:] and a[194:240]==b[162:208]
def make(mobile):
 w,h=(420,1230) if mobile else (1200,790);margin=28 if mobile else 44;plot=w-2*margin;scale=plot/1008;s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">','<title id="title">Reclay moves a title without losing its pointers</title>','<desc id="desc">One actual synthetic TIFF shrinks from 1,008 to 976 bytes. The marked title changes from 48 to 16 bytes; integer fields at 74, 90 and 138 become 16, 208 and 162. Independent decoding preserves the 32 by 24 pixels and private metadata.</desc>','<style>text{font-family:Arial,Helvetica,sans-serif;fill:#242d29}.muted{fill:#546257}.green{fill:#365e4d}.mono{font-family:"Courier New",Courier,monospace}</style>',f'<rect width="{w}" height="{h}" fill="#eee9df"/>']
 def text(x,y,v,size=20,cls='',weight=400):s.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" class="{cls}">{html.escape(v)}</text>')
 text(margin,48,'reclay',30,weight=700)
 if mobile:text(margin,103,'Move the title.',37,weight=700);text(margin,148,'Keep the image.',37,weight=700)
 else:text(margin,120,'Move the title. Keep the image.',54,weight=700)
 text(margin,185 if mobile else 164,'An actual file edit, down to the byte.',18,'muted')
 def bar(y,total,start,end,pixels,label):
  text(margin,y-16,label,22,weight=700)
  ranges=[(0,start,'#c5c9bc'),(start,end,'#365e4d'),(end,pixels,'#b3bdaa'),(pixels,total,'#d8dbce')]
  for l,u,color in ranges:s.append(f'<rect x="{margin+l*scale:.4f}" y="{y}" width="{(u-l)*scale:.4f}" height="50" fill="{color}" stroke="#657564" stroke-width=".8"/>')
  for offset in [0,start,pixels,total]:
   baseline=y+(97 if mobile and offset==pixels else 75)
   if mobile and offset in (start,pixels):
    boundary=margin+offset*scale
    s.append(f'<path d="M{boundary:.4f} {y+50}V{baseline-13}" stroke="#657564" stroke-width=".8"/>')
   text(margin+offset*scale-(38 if offset==total else 12 if mobile and offset==start else 0),baseline,str(offset),13,'mono')
 if mobile:
  bar(250,1008,146,194,240,'Original · 1,008 bytes');bar(400,976,146,162,208,'Edited · 976 bytes');text(margin,530,'Title 48 → 16 bytes',24,'green',700);text(margin,561,'Pixels move back 32 bytes; bytes stay exact.',15,'muted')
 else:
  bar(250,1008,146,194,240,'Original · 1,008 bytes');bar(410,976,146,162,208,'Edited · 976 bytes');text(margin,535,'Marked title 48 → 16 bytes. Pixels move back 32 bytes; their content stays exact.',23,'green',700)
 cards=[(74,48,16,'Byte count'),(90,240,208,'Image data address'),(138,194,162,'Private metadata address')]
 for i,(site,before,after,name) in enumerate(cards):
  x=margin if mobile else margin+i*378;y=615+i*166 if mobile else 606
  s.append(f'<path d="M{x} {y-20}H{x+(364 if mobile else 340)}" stroke="#8e9a8a"/>');text(x,y,name,22,weight=700);text(x,y+33,f'@{site}   {before} → {after}',22,'mono');text(x,y+67,a[site:site+4].hex(' ')+' →',18,'mono');text(x,y+93,b[site:site+4].hex(' '),18,'mono');text(x,y+121,'4-byte little-endian integer',15,'muted')
 if mobile:text(margin,1160,'Exact Pillow oracle. Standalone rebuild.',18,'green',700);text(margin,1194,'Document-family guards still apply.',17,'muted')
 else:text(margin,775,'Exact independent Pillow oracle; standalone Python rebuild. A fitted document family, not a universal format specification.',18,'muted')
 s.append('</svg>');return '\n'.join(s)+'\n'
for n,m in [('byte-connections',False),('byte-connections-mobile',True)]:(P/(n+'.svg')).write_text(make(m))
