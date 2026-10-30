import sys, zlib, struct, collections
for path in sys.argv[1:]:
    data = open(path,'rb').read(); n = len(data)//4096
    crc_ok = 0; types = collections.Counter(); z3 = z6 = 0; zero = 0
    for p in range(n):
        pg = data[p*4096:(p+1)*4096]
        if pg == bytes(4096): zero += 1; continue
        if struct.unpack('<I', pg[0xFFC:])[0] == zlib.crc32(pg[:0xFFC]): crc_ok += 1
        types[chr(pg[0xFF2]) if 32 <= pg[0xFF2] < 127 else hex(pg[0xFF2])] += 1
        z3 += pg[0xFF3] == 0; z6 += pg[0xFF6:0xFFC] == bytes(6)
    nz = n - zero
    print(f"{path.split('/')[-1]}: pages={n} zero={zero} crc_ok={crc_ok}/{nz} ff3=0:{z3} ff6..=0:{z6}")
    print("  types:", types.most_common(12))
