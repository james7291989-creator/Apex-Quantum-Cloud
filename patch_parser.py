import re
import io

file_path = r'C:\Dev\ApexQuantumCore\property_engine.py'
try:
    with io.open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Regex to capture the old parse_address function
    pattern = re.compile(r'def parse_address\(raw\):.*?(?=\ndef |\Z)', re.DOTALL)
    
    new_func = '''def parse_address(raw):
    # Extracts tokens from the street segment only. City/State will blind the GIS server.
    text = re.sub(r"\s+", " ", str(raw or "")).strip().upper()
    zip_match = re.search(r"\\b\d{5}\\b", text)
    zip_code = zip_match.group(0) if zip_match else ""
    
    # Split by comma, take ONLY the first part (the street)
    street_part = (text.split(",")[0] if "," in text else text).strip()
    
    num_match = re.search(r"^(\d+)", street_part)
    house_num = num_match.group(1) if num_match else ""
    
    rest = re.sub(r"^\d+", "", street_part).strip()
    if zip_code:
        rest = rest.replace(zip_code, "")
        
    noise = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST", "ST", "RD", "DR", "LN", "AVE", "BLVD", "CT", "WAY", "PL", "PKWY", "HWY", "STE"}
    tokens = [t for t in re.split(r"[, \s]+", rest) if t and len(t) > 1 and t not in noise]
    
    return house_num, tokens[:3], zip_code
'''
    
    if 'def parse_address(raw):' in content:
        new_content = pattern.sub(new_func, content, count=1)
        with io.open(file_path, 'w', encoding='utf-8', newline='') as f:
            f.write(new_content)
        print("[+] APEX HYDRA PATCH APPLIED: St. Louis County GIS token parsing is clean.")
    else:
        print("[-] Error: parse_address function not found.")
except Exception as e:
    print(f"Error: {e}")
