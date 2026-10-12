"""Evidence-bounded inference and lossless editing of tagged binary chunks.

The learner never identifies a format by magic, extension, or tag name. Its
hypothesis family is explicit; multiple explanations remain multiple models.
Model IDs identify exact configurations. They do not authenticate evidence or
prove who produced a model; externally supplied models are validated again.
"""
import hashlib
import itertools
import json
import zlib

MAX_FILE = 1024 * 1024
MAX_CORPUS = 8
MAX_CORPUS_BYTES = 4 * MAX_FILE
MAX_CHUNKS = 4096
MAX_MODELS = 128
MAX_MODEL_BYTES = 65536


class ReclayError(ValueError):
    def __init__(self, code, message, **context):
        super().__init__(message)
        self.code = code
        self.context = context

    def as_dict(self):
        return {"code": self.code, "message": str(self), "context": self.context}


def _fail(code, message, **context):
    raise ReclayError(code, message, **context)


def _integer(v, low, high, label):
    if type(v) is not int or not low <= v <= high:
        _fail("invalid-model", label+" is outside its allowed range")
    return v


def _hex(value, limit, label):
    if type(value) is not str or len(value) > 2*limit or len(value)%2:
        _fail("invalid-data", label+" must be bounded hexadecimal text")
    if any(c not in "0123456789abcdefABCDEF" for c in value):
        _fail("invalid-data", label+" contains non-hexadecimal characters")
    return bytes.fromhex(value)


def _blob(data):
    if type(data) is not bytes or not 0 < len(data) <= MAX_FILE:
        _fail("input-bound", "File must contain between 1 byte and 1 MiB")
    return data


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _model_id(model):
    return hashlib.sha256(_canon({k:v for k,v in model.items() if k!="id"}).encode()).hexdigest()


def _grammar(g):
    if type(g) is not dict:
        _fail("invalid-model", "Grammar must be an object")
    keys={"tagWidth","lengthWidth","endian","order","lengthMode","alignment","checksum"}
    if set(g)!=keys:
        _fail("invalid-model", "Grammar has unknown or missing properties")
    if g["tagWidth"] not in (2,4) or type(g["tagWidth"]) is not int:
        _fail("invalid-model", "Tag width must be 2 or 4")
    if g["lengthWidth"] not in (2,4) or type(g["lengthWidth"]) is not int:
        _fail("invalid-model", "Length width must be 2 or 4")
    if g["endian"] not in ("little","big") or g["order"] not in ("tag-length","length-tag"):
        _fail("invalid-model", "Unsupported byte order or header order")
    if g["lengthMode"] not in ("payload","includes-header"):
        _fail("invalid-model", "Unsupported length interpretation")
    if type(g["alignment"]) is not int or g["alignment"] not in (1,2,4):
        _fail("invalid-model", "Alignment must be 1, 2, or 4")
    c=g["checksum"]
    if c is not None:
        if type(c) is not dict or set(c)!={"scope","endian"}:
            _fail("invalid-model", "Checksum must have scope and endian")
        if c["scope"] not in ("payload","tag-payload","header-payload") or c["endian"] not in ("little","big"):
            _fail("invalid-model", "Unsupported CRC32 scope or byte order")
    return g


def validate_model(model):
    """Validate the bounded inference family; IDs are identities, not signatures."""
    if type(model) is not dict:
        _fail("invalid-model", "Model must be an object")
    try:
        if len(_canon(model).encode())>MAX_MODEL_BYTES:
            _fail("invalid-model", "Model exceeds 64 KiB")
    except (TypeError,ValueError,RecursionError):
        _fail("invalid-model", "Model must be bounded JSON data")
    required={"version","id","start","grammar","prefix","padding","nesting","evidence"}
    if set(model)!=required or model["version"]!=1 or type(model["version"]) is not int:
        _fail("invalid-model", "Unsupported model version or properties")
    start=_integer(model["start"],0,64,"Header start")
    _grammar(model["grammar"])
    prefix=model["prefix"]
    if type(prefix) is not dict or set(prefix)!={"templateHex","constantMaskHex","totalFields","unexplainedChangingOffsets","growthSafe"}:
        _fail("invalid-model", "Invalid prefix model")
    template=_hex(prefix["templateHex"],64,"Prefix template")
    mask=_hex(prefix["constantMaskHex"],64,"Prefix mask")
    if len(template)!=start or len(mask)!=start or any(x not in (0,255) for x in mask):
        _fail("invalid-model", "Prefix template and mask must match header start")
    if type(prefix["growthSafe"]) is not bool:
        _fail("invalid-model", "Growth policy must be a Boolean")
    fields=prefix["totalFields"]
    if type(fields) is not list or len(fields)>32:
        _fail("invalid-model", "Too many outer length fields")
    used=set()
    for field in fields:
        if type(field) is not dict or set(field)!={"offset","width","endian","bias"}:
            _fail("invalid-model", "Invalid outer length field")
        offset=_integer(field["offset"],0,start,"Outer field offset")
        if type(field["width"]) is not int or field["width"] not in (2,4,8) or offset+field["width"]>start:
            _fail("invalid-model", "Outer field lies outside prefix")
        if field["endian"] not in ("little","big"):
            _fail("invalid-model", "Invalid outer byte order")
        _integer(field["bias"],0,start,"Outer size bias")
        positions=set(range(offset,offset+field["width"]))
        if used&positions or any(mask[x]!=0 for x in positions):
            _fail("invalid-model", "Outer fields overlap or conflict with constant prefix bytes")
        used|=positions
    unexplained=prefix["unexplainedChangingOffsets"]
    if type(unexplained) is not list or len(unexplained)>64:
        _fail("invalid-model", "Invalid unexplained prefix offsets")
    for x in unexplained:
        _integer(x,0,start-1,"Unexplained prefix offset")
        if x in used or mask[x]!=0:
            _fail("invalid-model", "Unexplained prefix offset conflicts with a field")
    if len(set(unexplained))!=len(unexplained) or (unexplained and prefix["growthSafe"]):
        _fail("invalid-model", "Inconsistent prefix growth policy")
    if {i for i,x in enumerate(mask) if x==0}!=used|set(unexplained):
        _fail("invalid-model", "Prefix mask has unconstrained bytes")
    padding=model["padding"]
    if type(padding) is not dict or set(padding)!={"zeroObserved"} or type(padding["zeroObserved"]) is not bool:
        _fail("invalid-model", "Invalid padding evidence")
    nesting=model["nesting"]
    if type(nesting) is not list or len(nesting)>32:
        _fail("invalid-model", "Too many container hypotheses")
    tags=set()
    for rule in nesting:
        if type(rule) is not dict or set(rule)!={"tagHex","prefixHex","grammar","zeroPaddingObserved"}:
            _fail("invalid-model", "Invalid nested chunk hypothesis")
        tag=_hex(rule["tagHex"],4,"Container tag")
        if rule["tagHex"]!=tag.hex() or len(tag)!=model["grammar"]["tagWidth"] or not all(32<=x<=126 for x in tag) or tag in tags:
            _fail("invalid-model", "Invalid or duplicate container tag")
        tags.add(tag)
        container_prefix=_hex(rule["prefixHex"],16,"Container prefix")
        if rule["prefixHex"]!=container_prefix.hex():
            _fail("invalid-model", "Container prefix must use canonical lowercase hexadecimal text")
        _grammar(rule["grammar"])
        if rule["grammar"]!=model["grammar"]:
            _fail("invalid-model", "Nested chunks must use the same inferred grammar as their parent")
        if type(rule["zeroPaddingObserved"]) is not bool:
            _fail("invalid-model", "Invalid nested padding evidence")
    evidence=model["evidence"]
    if type(evidence) is not dict or set(evidence)!={"samples","hypothesisFamily","nestingAmbiguities"}:
        _fail("invalid-model", "Invalid model evidence")
    samples=evidence["samples"]
    if type(samples) is not list or not 2<=len(samples)<=MAX_CORPUS:
        _fail("invalid-model", "Model needs evidence from 2 to 8 samples")
    total=0; hashes=set()
    for sample in samples:
        if type(sample) is not dict or set(sample)!={"size","sha256"}:
            _fail("invalid-model", "Invalid sample evidence")
        total+=_integer(sample["size"],1,MAX_FILE,"Sample size")
        h=sample["sha256"]
        if type(h) is not str or len(h)!=64 or any(c not in "0123456789abcdef" for c in h):
            _fail("invalid-model", "Invalid sample SHA256")
        hashes.add(h)
    if total>MAX_CORPUS_BYTES or len(hashes)<2:
        _fail("invalid-model", "Corpus evidence exceeds bounds or lacks distinct samples")
    varying_lengths=len({sample["size"] for sample in samples})>1
    expected_growth=not unexplained and (varying_lengths or start==0)
    if prefix["growthSafe"]!=expected_growth:
        _fail("invalid-model", "Prefix growth policy differs from its bounded corpus evidence")
    if type(evidence["hypothesisFamily"]) is not str or len(evidence["hypothesisFamily"])>1024:
        _fail("invalid-model", "Invalid hypothesis description")
    ambiguities=evidence["nestingAmbiguities"]
    if type(ambiguities) is not list or len(ambiguities)>32:
        _fail("invalid-model", "Invalid nesting ambiguity evidence")
    if model["id"]!=_model_id(model):
        _fail("invalid-model", "Model ID does not match its exact configuration")
    return model


def _crc(header,tag,payload,scope):
    if scope=="payload":return zlib.crc32(payload)&0xffffffff
    lead=tag if scope=="tag-payload" else header
    return zlib.crc32(payload,zlib.crc32(lead))&0xffffffff


def _read_chunks(data,g):
    """Read exactly the supplied span; no ignored prefix/trailing bytes."""
    tw=g["tagWidth"];lw=g["lengthWidth"];hw=tw+lw
    chunks=[];at=0
    while at<len(data):
        if len(chunks)>=MAX_CHUNKS:
            _fail("chunk-bound", "Span exceeds 4096 chunks")
        if len(data)-at<hw:
            _fail("truncated-header", "Incomplete chunk header", offset=at)
        header=data[at:at+hw]
        if g["order"]=="tag-length":tag=header[:tw];rawsize=header[tw:]
        else:rawsize=header[:lw];tag=header[lw:]
        if not all(32<=x<=126 for x in tag):
            _fail("invalid-tag", "Tag is outside the printable ASCII hypothesis", offset=at)
        length=int.from_bytes(rawsize,g["endian"])
        payload_size=length-(hw if g["lengthMode"]=="includes-header" else 0)
        tail=4 if g["checksum"] else 0
        if payload_size<0 or payload_size>len(data)-at-hw-tail:
            _fail("invalid-length", "Chunk length exceeds its enclosing span", offset=at)
        end=at+hw+payload_size
        payload=data[at+hw:end]
        checksum=data[end:end+tail]
        if tail:
            c=g["checksum"]
            if int.from_bytes(checksum,c["endian"])!=_crc(header,tag,payload,c["scope"]):
                _fail("checksum-mismatch", "CRC32 does not match the inferred scope", offset=at)
        pad_size=(-payload_size)%g["alignment"]
        stop=end+tail+pad_size
        if stop>len(data):
            _fail("truncated-padding", "Missing chunk alignment bytes", offset=at)
        chunks.append({"tag":tag,"payload":payload,"padding":data[end+tail:stop],"offset":at})
        at=stop
    return chunks


def _prefix_models(samples,start,*,with_trace=False):
    prefixes=[b[:start] for b in samples]
    varying={i for i in range(start) if len({b[i] for b in prefixes})>1}
    varying_lengths=len({len(b) for b in samples})>1
    possible=[]
    if varying_lengths:
        for width in (2,4,8):
            for offset in range(start-width+1):
                positions=set(range(offset,offset+width))
                if not positions&varying:continue
                for endian in ("little","big"):
                    biases=[len(b)-int.from_bytes(b[offset:offset+width],endian) for b in samples]
                    # A total-length field's subtraction constant is required to
                    # lie within the prefix. Larger relationships need labelled
                    # interventions rather than accidental affine agreement.
                    if len(set(biases))==1 and 0<=biases[0]<=start:
                        possible.append({"offset":offset,"width":width,"endian":endian,"bias":biases[0]})
    selections=[];prefix_bound_reached=False
    def choose(remaining,selected,used):
        nonlocal prefix_bound_reached
        if prefix_bound_reached:return
        if not remaining:
            if len(selections)<32:selections.append(selected)
            else:prefix_bound_reached=True
            return
        at=min(remaining)
        options=[f for f in possible if f["offset"]<=at<f["offset"]+f["width"] and
                 not used&set(range(f["offset"],f["offset"]+f["width"]))]
        if not options:
            choose(remaining-{at},selected,used);return
        for f in options:
            positions=set(range(f["offset"],f["offset"]+f["width"]))
            choose(remaining-positions,selected+[f],used|positions)
    choose(varying,[],set())
    if not selections:selections=[[]]
    output=[];seen=set()
    for selected in selections:
        selected=sorted(selected,key=lambda f:f["offset"])
        fingerprint=_canon(selected)
        if fingerprint in seen:continue
        seen.add(fingerprint)
        covered=set().union(*(set(range(f["offset"],f["offset"]+f["width"])) for f in selected)) if selected else set()
        unexplained=sorted(varying-covered)
        mask=bytes(0 if i in varying|covered else 255 for i in range(start))
        output.append({"templateHex":prefixes[0].hex(),"constantMaskHex":mask.hex(),"totalFields":selected,
                       "unexplainedChangingOffsets":unexplained,
                       "growthSafe":not unexplained and (varying_lengths or start==0)})
    return (output,prefix_bound_reached) if with_trace else output


def _padding_evidence(groups):
    pads=[c["padding"] for chunks in groups for c in chunks if c["padding"]]
    return bool(pads) and all(not any(p) for p in pads)


def _nesting(groups,g):
    bytag={}
    for chunks in groups:
        for c in chunks:bytag.setdefault(c["tag"],[]).append(c["payload"])
    rules=[];ambiguities=[]
    for tag,payloads in bytag.items():
        if len(payloads)<2:continue
        options=[]
        for skip in (0,4,8,12,16):
            if any(len(p)<=skip for p in payloads) or len({p[:skip] for p in payloads})!=1:continue
            try:
                children=[_read_chunks(p[skip:],g) for p in payloads]
                if any(len(cs)<2 for cs in children):continue
                options.append({"tagHex":tag.hex(),"prefixHex":payloads[0][:skip].hex(),"grammar":dict(g),
                                "zeroPaddingObserved":_padding_evidence(children)})
            except ReclayError:pass
        if len(options)==1:rules.append(options[0])
        elif len(options)>1:ambiguities.append({"tagHex":tag.hex(),"prefixLengths":[len(bytes.fromhex(o["prefixHex"])) for o in options]})
    return rules,ambiguities


def _experiments(models,samples,truncated):
    suggestions=[]
    grammars={_canon(m["grammar"]) for m in models}
    alignments={m["grammar"]["alignment"] for m in models}
    if len(alignments)>1 or any(m["grammar"]["alignment"]>1 and not m["padding"]["zeroObserved"] for m in models):
        suggestions.append({"kind":"odd-payload","instruction":"Save the same object with a known payload changed to an odd byte length; also try lengths 2 and 3 modulo 4.","reason":"Separates alignment rules and establishes padding bytes."})
    if any(f["width"]==2 for m in models for f in m["prefix"]["totalFields"]) or any(m["grammar"]["lengthWidth"]==2 for m in models):
        suggestions.append({"kind":"width-boundary","instruction":"Save a controlled payload longer than 65535 bytes, within the 1 MiB file limit.","reason":"Separates two-byte lengths from four-byte lengths and reveals width transitions."})
    if len(models)>1 or any(not m["prefix"]["growthSafe"] for m in models):
        suggestions.append({"kind":"earlier-block-growth","instruction":"Change only the earliest known variable block, keeping later values unchanged; provide both files and the changed value.","reason":"Tests prefix length relationships, chunk boundaries, and displaced offsets."})
    if not suggestions:
        suggestions.append({"kind":"held-out-edit","instruction":"Provide a fresh file with different values, then resize one known payload and compare the rebuilt file with an independent save from the original application.","reason":"A fitting grammar is a hypothesis, not proof of unknown field semantics."})
    if truncated:suggestions.append({"kind":"search-bound","instruction":"Supply more discriminating samples before selecting a model.","reason":"A finite hypothesis bound was reached; alternatives were omitted, so this is not an exhaustive list."})
    return suggestions


def infer(corpus, *, max_models=MAX_MODELS):
    """Return every bounded surviving hypothesis, never silently select one."""
    if type(corpus) is not list or not 2<=len(corpus)<=MAX_CORPUS:
        _fail("corpus-bound", "Provide 2 to 8 distinct binary samples")
    _integer(max_models,1,MAX_MODELS,"Model limit")
    samples=[_blob(b) for b in corpus]
    if sum(map(len,samples))>MAX_CORPUS_BYTES:
        _fail("corpus-bound", "Corpus exceeds 4 MiB")
    receipts=[{"size":len(b),"sha256":hashlib.sha256(b).hexdigest()} for b in samples]
    if len({r["sha256"] for r in receipts})<2:
        _fail("corpus-bound", "Corpus needs at least two byte-distinct files")
    checksums=[None]+[{"scope":scope,"endian":endian} for scope,endian in itertools.product(("payload","tag-payload","header-payload"),("little","big"))]
    models=[];model_bound_reached=False;prefix_bound_reached=False
    family="Printable 2/4-byte tags; 2/4-byte integers; LE/BE; tag/length order; payload/header-inclusive length; 1/2/4 alignment; optional standard CRC32 tail; start 0..64; one nested level using the same chunk grammar."
    for start in range(min(64,min(map(len,samples)))+1):
        for tw,lw,endian,order,mode,alignment in itertools.product((2,4),(2,4),("little","big"),("tag-length","length-tag"),("payload","includes-header"),(1,2,4)):
            # Cheap first-header filter before allocating payload slices or CRC.
            hw=tw+lw
            if any(len(b)-start<hw for b in samples):continue
            headers=[b[start:start+hw] for b in samples]
            tags=[h[:tw] if order=="tag-length" else h[lw:] for h in headers]
            if any(not all(32<=x<=126 for x in t) for t in tags):continue
            sizes=[int.from_bytes(h[tw:] if order=="tag-length" else h[:lw],endian)-(hw if mode=="includes-header" else 0) for h in headers]
            if any(n<0 or n>len(b)-start-hw for n,b in zip(sizes,samples)):continue
            for checksum in checksums:
                g={"tagWidth":tw,"lengthWidth":lw,"endian":endian,"order":order,"lengthMode":mode,"alignment":alignment,"checksum":checksum}
                try:
                    groups=[_read_chunks(b[start:],g) for b in samples]
                    if any(len(cs)<2 for cs in groups):continue
                except ReclayError:continue
                nesting,ambiguities=_nesting(groups,g)
                prefixes,prefix_capped=_prefix_models(samples,start,with_trace=True)
                prefix_bound_reached=prefix_bound_reached or prefix_capped
                for prefix in prefixes:
                    model={"version":1,"start":start,"grammar":g,"prefix":prefix,"padding":{"zeroObserved":_padding_evidence(groups)},
                           "nesting":nesting,"evidence":{"samples":receipts,"hypothesisFamily":family,"nestingAmbiguities":ambiguities}}
                    model["id"]=_model_id(model)
                    # Actual lossless reserialization validates every surviving
                    # model, including opaque padding and optional containers.
                    try:
                        if any(rebuild(parse(b,model),model)!=b for b in samples):continue
                    except ReclayError:continue
                    models.append(model)
                    if len(models)>=max_models:
                        model_bound_reached=True;break
                if model_bound_reached:break
            if model_bound_reached:break
        if model_bound_reached:break
    truncated=model_bound_reached or prefix_bound_reached
    truncation_reasons=[]
    if model_bound_reached:truncation_reasons.append({"kind":"modelLimit","limit":max_models})
    if prefix_bound_reached:truncation_reasons.append({"kind":"prefixSelections","limit":32})
    status="unsupported" if not models else ("unique" if len(models)==1 and not truncated else "ambiguous")
    return {"status":status,"models":models,"evidence":receipts,"truncated":truncated,"truncationReasons":truncation_reasons,
            "experiments":_experiments(models,samples,truncated),"limits":{"fileBytes":MAX_FILE,"corpusFiles":MAX_CORPUS,"corpusBytes":MAX_CORPUS_BYTES,"chunksPerSpan":MAX_CHUNKS,"models":max_models}}


def _json_chunks(chunks,model):
    rules={r["tagHex"]:r for r in model["nesting"]}
    result=[]
    total_chunks=len(chunks)
    for c in chunks:
        out={"tagHex":c["tag"].hex(),"payloadHex":c["payload"].hex(),"paddingHex":c["padding"].hex()}
        rule=rules.get(out["tagHex"])
        if rule:
            prefix=bytes.fromhex(rule["prefixHex"])
            if not c["payload"].startswith(prefix):
                _fail("container-prefix", "Container prefix differs from model evidence")
            nested=_read_chunks(c["payload"][len(prefix):],rule["grammar"])
            if len(nested)<2:_fail("container-shape", "Container does not contain two inferred chunks")
            total_chunks+=len(nested)
            if total_chunks>MAX_CHUNKS:_fail("chunk-bound", "Document exceeds 4096 total chunks")
            out["containerPrefixHex"]=prefix.hex()
            out["children"]=[{"tagHex":n["tag"].hex(),"payloadHex":n["payload"].hex(),"paddingHex":n["padding"].hex()} for n in nested]
        result.append(out)
    return result


def parse(data,model):
    validate_model(model);_blob(data)
    start=model["start"]
    if len(data)<start:_fail("prefix-bound", "File is shorter than its inferred prefix")
    prefix=data[:start];p=model["prefix"]
    template=bytes.fromhex(p["templateHex"]);mask=bytes.fromhex(p["constantMaskHex"])
    if any(m and a!=b for a,b,m in zip(prefix,template,mask)):
        _fail("prefix-mismatch", "A constant prefix byte differs from training evidence")
    for f in p["totalFields"]:
        expected=len(data)-f["bias"]
        if expected<0 or int.from_bytes(prefix[f["offset"]:f["offset"]+f["width"]],f["endian"])!=expected:
            _fail("outer-size-mismatch", "Outer size field differs from the inferred relation",offset=f["offset"])
    chunks=_read_chunks(data[start:],model["grammar"])
    if len(chunks)<2:_fail("chunk-evidence", "File must contain at least two inferred chunks")
    return {"modelId":model["id"],"prefixHex":prefix.hex(),"chunks":_json_chunks(chunks,model),
            "evidence":{"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}}


def _validate_document(doc,model):
    if type(doc) is not dict or set(doc)!={"modelId","prefixHex","chunks","evidence"} or doc["modelId"]!=model["id"]:
        _fail("invalid-document", "Document must match its exact inferred model")
    prefix=_hex(doc["prefixHex"],64,"Document prefix")
    if len(prefix)!=model["start"]:_fail("invalid-document", "Document prefix size differs from model")
    ev=doc["evidence"]
    if type(ev) is not dict or set(ev)!={"size","sha256"}:
        _fail("invalid-document", "Document lacks original-byte evidence")
    _integer(ev["size"],1,MAX_FILE,"Original file size")
    h=ev["sha256"]
    if type(h) is not str or len(h)!=64 or any(c not in "0123456789abcdef" for c in h):
        _fail("invalid-document", "Invalid document SHA256")
    chunks=doc["chunks"]
    if type(chunks) is not list or not 2<=len(chunks)<=MAX_CHUNKS:
        _fail("invalid-document", "Document chunk count exceeds bounds")
    rules={r["tagHex"]:r for r in model["nesting"]}
    total=0;count=0
    def check(nodes,g,nested=False):
        nonlocal total,count
        for node in nodes:
            count+=1
            if count>MAX_CHUNKS:_fail("chunk-bound", "Document exceeds 4096 total chunks")
            if type(node) is not dict:_fail("invalid-document", "Chunk must be an object")
            container="children" in node
            allowed={"tagHex","payloadHex","paddingHex"}|({"children","containerPrefixHex"} if container else set())
            if set(node)!=allowed:_fail("invalid-document", "Chunk has missing or unknown properties")
            tag=_hex(node["tagHex"],4,"Chunk tag")
            payload=_hex(node["payloadHex"],MAX_FILE,"Chunk payload")
            pad=_hex(node["paddingHex"],3,"Chunk padding")
            total+=len(payload)+len(pad)+g["tagWidth"]+g["lengthWidth"]+(4 if g["checksum"] else 0)
            if total>2*MAX_FILE:_fail("input-bound", "Document hex data exceeds bounds")
            if node["tagHex"]!=tag.hex() or len(tag)!=g["tagWidth"] or not all(32<=x<=126 for x in tag):_fail("invalid-document", "Chunk tag differs from canonical inferred grammar")
            if len(pad)!=(-len(payload))%g["alignment"]:_fail("invalid-document", "Chunk padding size differs from grammar")
            rule=rules.get(node["tagHex"]) if not nested else None
            if bool(rule)!=container:_fail("invalid-document", "Container structure differs from model")
            if container:
                pre=_hex(node["containerPrefixHex"],16,"Container prefix")
                if node["containerPrefixHex"]!=pre.hex() or pre.hex()!=rule["prefixHex"] or type(node["children"]) is not list or not 2<=len(node["children"])<=MAX_CHUNKS:
                    _fail("invalid-document", "Invalid container children or prefix")
                check(node["children"],rule["grammar"],True)
    check(chunks,model["grammar"])
    return prefix


def _build_nodes(nodes,g,zero_observed,rules,edits,parent=()):
    out=[]
    hw=g["tagWidth"]+g["lengthWidth"]
    for i,node in enumerate(nodes):
        path=parent+(i,);tag=bytes.fromhex(node["tagHex"]);original=bytes.fromhex(node["payloadHex"])
        if "children" in node:
            if path in edits:_fail("container-edit", "Edit leaf payloads inside a container, not its opaque parent",path=list(path))
            rule=rules[node["tagHex"]]
            raw=bytes.fromhex(node["containerPrefixHex"])+_build_nodes(node["children"],rule["grammar"],rule["zeroPaddingObserved"],{},edits,path)
            # A document cannot misrepresent the original container bytes.
            unchanged_children=_build_nodes(node["children"],rule["grammar"],rule["zeroPaddingObserved"],{}, {},path)
            if bytes.fromhex(node["containerPrefixHex"])+unchanged_children!=original:
                _fail("invalid-document", "Container payload does not match its original child bytes")
        else:raw=edits.get(path,original)
        length=len(raw)+(hw if g["lengthMode"]=="includes-header" else 0)
        if length>=1<<(8*g["lengthWidth"]):
            _fail("length-overflow", "Edit exceeds the inferred length-field width",path=list(path))
        size=length.to_bytes(g["lengthWidth"],g["endian"])
        header=tag+size if g["order"]=="tag-length" else size+tag
        tail=b""
        if g["checksum"]:
            c=g["checksum"];tail=_crc(header,tag,raw,c["scope"]).to_bytes(4,c["endian"])
        required=(-len(raw))%g["alignment"]
        oldpad=bytes.fromhex(node["paddingHex"])
        if required==len(oldpad):pad=oldpad
        elif required==0:pad=b""
        elif zero_observed:pad=bytes(required)
        else:_fail("padding-unproven", "Resizing needs new padding bytes that the corpus did not establish",path=list(path))
        out.append(header+raw+tail+pad)
    result=b"".join(out)
    if len(result)>MAX_FILE:_fail("input-bound", "Rebuilt span exceeds 1 MiB")
    return result


def rebuild(document,model,edits=None):
    """Rebuild from a hash-bound parsed document plus explicit raw leaf edits."""
    validate_model(model)
    prefix=_validate_document(document,model)
    if edits is None:edits=[]
    if type(edits) is not list or len(edits)>256:_fail("edit-bound", "Edits must be a list of at most 256 leaf replacements")
    replacements={};total=0
    for edit in edits:
        if type(edit) is not dict or set(edit)!={"path","payloadHex"} or type(edit["path"]) is not list or not 1<=len(edit["path"])<=2:
            _fail("invalid-edit", "Edit needs a one/two-index chunk path and payloadHex")
        path=tuple(_integer(x,0,MAX_CHUNKS-1,"Edit path index") for x in edit["path"])
        if path in replacements:_fail("invalid-edit", "Duplicate edit path")
        nodes=document["chunks"]
        for depth,index in enumerate(path):
            if index>=len(nodes):_fail("invalid-edit", "Edit path does not exist")
            node=nodes[index]
            if depth<len(path)-1:
                if "children" not in node:_fail("invalid-edit", "Edit path crosses a leaf")
                nodes=node["children"]
        if "children" in node:_fail("container-edit", "Replace a leaf within the container")
        value=_hex(edit["payloadHex"],MAX_FILE,"Edited payload")
        total+=len(value)
        if total>MAX_FILE:_fail("edit-bound", "Combined edited payloads exceed 1 MiB")
        replacements[path]=value
    rules={r["tagHex"]:r for r in model["nesting"]}
    original_body=_build_nodes(document["chunks"],model["grammar"],model["padding"]["zeroObserved"],rules,{})
    original=prefix+original_body
    if len(original)!=document["evidence"]["size"] or hashlib.sha256(original).hexdigest()!=document["evidence"]["sha256"]:
        _fail("document-tampered", "Document bytes do not match their original SHA256 evidence")
    # Independently validate prefix constraints and real chunks even when callers
    # supply a manufactured JSON document rather than using parse().
    parse(original,model)
    body=_build_nodes(document["chunks"],model["grammar"],model["padding"]["zeroObserved"],rules,replacements)
    final_size=len(prefix)+len(body)
    if final_size>MAX_FILE:_fail("input-bound", "Rebuilt file exceeds 1 MiB")
    if final_size!=len(original) and not model["prefix"]["growthSafe"]:
        _fail("unexplained-prefix", "Growth is refused because observed prefix changes have no established length relation")
    updated=bytearray(prefix)
    for f in model["prefix"]["totalFields"]:
        value=final_size-f["bias"]
        if not 0<=value<1<<(8*f["width"]):
            _fail("outer-overflow", "Edit exceeds the inferred outer-length width",offset=f["offset"])
        updated[f["offset"]:f["offset"]+f["width"]]=value.to_bytes(f["width"],f["endian"])
    result=bytes(updated)+body
    parse(result,model)
    return result
