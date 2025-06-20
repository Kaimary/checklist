import json

# Load JSON file (assumed to be an array of JSON objects)
with open("data/minidev/mini_dev_sqlite.json", "r", encoding="utf-8") as infile:
    data = json.load(infile)

# Write to JSONL file
with open("data/minidev/mini_dev_sqlite.jsonl", "w", encoding="utf-8") as outfile:
    for entry in data:
        json_line = json.dumps(entry)
        outfile.write(json_line + "\n")