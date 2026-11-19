import re
import zipfile

mph_path = r"E:\comsol_scratch\recoveries\tmp15707525736246110729.mph"
print(f"Reading: {mph_path}")
with zipfile.ZipFile(mph_path, "r") as z:
    names = z.namelist()
    print(f"Total files in zip: {len(names)}")
    print(f"First 10 files: {names[:10]}")
    if "dmodel.xml" in names:
        dmodel = z.read("dmodel.xml").decode("utf-8", errors="replace")
        print(f"dmodel.xml length: {len(dmodel)}")
        m_tag = re.search(r'name="([^"]+)"', dmodel[:500])
        print(f"Model name: {m_tag.group(1) if m_tag else 'unknown'}")
        sol_matches = re.findall(r'<pvals[^>]*>([^<]+)</pvals>', dmodel)
        print(f"pvals elements: {len(sol_matches)}")
        for i, sm in enumerate(sol_matches):
            tokens = sm.strip().split()
            print(f"  sol #{i+1}: count={len(tokens)}, min={tokens[0] if tokens else '?'}, max={tokens[-1] if tokens else '?'}")
