import sys, collections
def dom_step(s):
    c=collections.Counter((s[i+1]-s[i])&255 for i in range(len(s)-1)); return c.most_common(1)[0][0]
def rbase(s,st):
    c=collections.Counter((b-i*st)&255 for i,b in enumerate(s)); return c.most_common(1)[0][0]
def purity(s,b,st): return sum(1 for i,x in enumerate(s) if x==(b+i*st)&255)/len(s)
for path in sys.argv[1:]:
    d=open(path,'rb').read(); n=len(d)//4096
    blocks=collections.defaultdict(collections.Counter); pure=tot=0; steps=collections.Counter()
    for pn in range(1,n):
        pg=d[pn*4096:(pn+1)*4096]; t=pg[0xFF2]&0xBF
        if t not in (0,3,8,0xd): continue
        for si in range(8):
            sec=pg[si*512:(si*512+512) if si<7 else 0xFF0]; tot+=1
            st=dom_step(sec); b=rbase(sec,st)
            if purity(sec,b,st)<1.0: continue
            pure+=1; steps[st]+=1
            bv=(b-pn-si+4*((pn%16)//2))&255
            blocks[pn//16][bv]+=1
    cons=sum(1 for c in blocks.values() if len(c)==1)
    print(f"{path.split('/')[-1]}: pure sectors {pure}/{tot}; blocks with votes {len(blocks)} of {n//16+1}; single-bv blocks {cons}")
    print("  sample block bv votes:", [(k,dict(v)) for k,v in sorted(blocks.items())[:8]])
    print("  top steps:", steps.most_common(8))
