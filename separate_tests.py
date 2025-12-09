import json

judgment_file_path = f"data/spider/results/judgments,dataset=spider+resdsql,judge=guardian(mtp).jsonl"
judgments = [json.loads(line) for line in open(judgment_file_path)]
output_file_path = f"data/spider/results/judgments,dataset=spider+resdsql,judge=guardian(mtp)(strengthen).jsonl"
judgments1 = open(output_file_path, 'a+')
for ex in judgments:
    res = ex["MTP Test_details"]["Natural Language Strengthening Test Class"]
    if res['total'] == 0:
        judgment = "UNDETERMINED"
    else:
        judgment = True if res['passed'] == res['total'] else False
    ret = {
        f"MTP Test": judgment,
        f"MTP Test_details": {"Natural Language Strengthening Test Class": res},
        "judgment": judgment
    }
    json.dump(ret, judgments1)
    judgments1.write('\n')
    judgments1.flush()