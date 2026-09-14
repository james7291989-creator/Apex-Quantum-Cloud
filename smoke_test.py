from titan_quant import TitanQuantEngine

test_input = "3bed 2bath 7740 D Rd, Waterloo, IL 62298 Property type Mobile Year built 1981 Last sold $89K in 2016 Water Lot Size: 77 Acres"
print(">>> INGESTING RAW UNSTRUCTURED STREAM:")
print(f"    '{test_input}'\n")

params = TitanQuantEngine.parse_raw_telemetry(test_input)
print(f"[+] PARSER HARVEST:")
print(f"    Acres        : {params['acres']}")
print(f"    SqFt (Synth) : {params['sqft']} (Synthetic: {params['is_synthetic_sqft']})")
print(f"    Year Built   : {params['year_built']}")
print(f"    Property Type: {params['property_type']}")
print(f"    Condition    : {params['condition']}")

result = TitanQuantEngine.execute_underwrite(params)
print(result["analysis"])

assert result["arv"] > 500000, "77-Acre property must evaluate land value!"
assert result["base_mao"] > 0, "MAO must calculate cleanly without divide-by-zero!"
print("\n[>>>] SMOKE TEST PASSED WITH ZERO COMPILER OR LOGIC FAULTS.")
