import sys, json
src, K, steps_path, dst = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
steps={int(k):v for k,v in json.load(open(steps_path)).items()}
d=bytearray(open(src,'rb').read()); n=len(d)//4096
for pn in range(1,n):
    for si in range(8):
        off=pn*4096+si*512; end=pn*4096+(512*(si+1) if si<7 else 0xFF0)
        b=(K+pn+si)&255; st=steps.get((pn+si+1)//2,1)
        for i in range(end-off): d[off+i]=(d[off+i]-b-i*st)&255
open(dst,'wb').write(d)
