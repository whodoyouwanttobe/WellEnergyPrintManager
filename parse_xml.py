import xml.etree.ElementTree as ET
try:
    tree = ET.parse('dist/canon_caps.xml')
    root = tree.getroot()
    ns = {'psf': 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework'}
    features = root.findall('.//psf:Feature[@name="psk:PageMediaSize"]/psf:Option', ns)
    print("Available PageMediaSize Options (first 50):")
    for opt in features[:50]:
        name = opt.attrib.get('name', 'Unknown')
        print(f" - {name}")
except Exception as e:
    print(f"Error: {e}")
